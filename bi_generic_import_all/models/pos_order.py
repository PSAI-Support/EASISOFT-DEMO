# -*- coding: utf-8 -*-
# Part of BrowseInfo. See LICENSE file for full copyright and licensing details.

import time
import datetime
import tempfile
import binascii
import xlrd
import io
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT, DEFAULT_SERVER_DATE_FORMAT
import datetime
# from datetime import date, datetime
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from odoo import models, fields, exceptions, api, _
import logging

_logger = logging.getLogger(__name__)

try:
    import csv
except ImportError:
    _logger.debug('Cannot `import csv`.')
try:
    import cStringIO
except ImportError:
    _logger.debug('Cannot `import cStringIO`.')
try:
    import base64
except ImportError:
    _logger.debug('Cannot `import base64`.')


class PosOrderLines(models.Model):
    _inherit = "pos.order.line"

    def _compute_amount_line_all(self):
        self.ensure_one()
        fpos = self.order_id.fiscal_position_id
        tax_ids_after_fiscal_position = fpos.map_tax(self.tax_ids)
        price = self.price_unit * (1 - (self.discount or 0.0) / 100.0)
        taxes = tax_ids_after_fiscal_position.compute_all(price, self.order_id.pricelist_id.currency_id, self.qty,
                                                          product=self.product_id, partner=self.order_id.partner_id)
        if taxes['taxes']:
            return {
                'price_subtotal_incl': taxes['total_included'],
                'price_subtotal': taxes['total_excluded'],
                'taxes': taxes['taxes']
            }
        else:
            return {
                'price_subtotal_incl': taxes['total_included'],
                'price_subtotal': taxes['total_excluded'],
            }


class import_pos_order(models.Model):
    _inherit = "pos.order"

    is_import = fields.Boolean(string="imported data", default=False)


class gen_pos_order(models.TransientModel):
    _name = "gen.pos.order"
    _description = "Gen Pos Order"

    file_to_upload = fields.Binary('File')
    import_option = fields.Selection([('csv', 'CSV File'), ('xls', 'XLS File')], string='Select', default='csv')

    def find_session_id(self, session):
        if session:
            session_ids = self.env['pos.session'].search([('name', '=', session)])
        else:
            raise ValidationError(_('Wrong Session %s') % session)

        if session_ids:
            session_id = session_ids[0]
            return session_id
        else:
            raise ValidationError(_('Wrong Session %s') % session)

    def find_partner(self, partner_name):
        partner_ids = self.env['res.partner'].search([('name', '=', partner_name)])
        if len(partner_ids) != 0:
            partner_id = partner_ids[0]
            return partner_id
        else:
            raise ValidationError(_('Wrong Partner %s') % partner_name)

    def check_product(self, product):
        product_ids = self.env['product.product'].search([('name', '=', product)])
        if product_ids:
            product_id = product_ids[0]
            return product_id
        else:
            raise ValidationError(_('Wrong Product %s') % product)

    def find_sales_person(self, name):
        sals_person_obj = self.env['res.users']
        partner_search = sals_person_obj.search([('name', '=', name)])
        if partner_search:
            return partner_search
        else:
            raise ValidationError(_('Not Valid Salesperson Name "%s"') % name)

    def make_pos_date(self, date):
        DATETIME_FORMAT = '%m/%d/%Y %H:%M:%S'
        if date:
            try:
                i_date = datetime.datetime.strptime(date, DATETIME_FORMAT)
            except Exception:
                raise ValidationError(_('Wrong Date Format. Date Should be in format MM/DD/YYYY H:M:S.'))
            return i_date
        else:
            raise ValidationError(_('Date field is blank in sheet Please add the date.'))

    def make_pos(self, values):
        pos_obj = self.env['pos.order']
        partner_id = self.find_partner(values.get('partner_id'))
        salesperson_id = self.find_sales_person(values.get('salesperson'))
        session_id = self.find_session_id(values.get('session'))
        i_date = self.make_pos_date(values.get('date_order'))

        if partner_id and salesperson_id and session_id:
            pos_search = pos_obj.search([('partner_id', '=', partner_id.id), ('session_id', '=', session_id.id),
                                         ('user_id', '=', salesperson_id.id), ('name', '=', values.get('name'))])
            if pos_search:
                pos_search = pos_search[0]
                pos_id = pos_search
            else:

                pos_id = pos_obj.create({
                    'name': values.get('name'),
                    'partner_id': partner_id.id or False,
                    'user_id': salesperson_id.id or False,
                    'session_id': session_id.id or False,
                    'date_order': i_date,
                    'amount_paid': 0.0,
                    'amount_return': 0.0,
                    'amount_tax': 0.0,
                    'amount_total': 0.0,
                    'is_import': True
                })
                main_list = values.keys()
                for i in main_list:
                    model_id = self.env['ir.model'].search([('model', '=', 'pos.order')])
                    if type(i) == bytes:
                        normal_details = i.decode('utf-8')
                    else:
                        normal_details = i
                    if normal_details.startswith('x_'):
                        any_special = self.check_splcharacter(normal_details)
                        if any_special:
                            split_fields_name = normal_details.split("@")
                            technical_fields_name = split_fields_name[0]
                            many2x_fields = self.env['ir.model.fields'].search(
                                [('name', '=', technical_fields_name), ('state', '=', 'manual'),
                                 ('model_id', '=', model_id.id)])
                            if many2x_fields.id:
                                if many2x_fields.ttype in ['many2one', 'many2many']:
                                    if many2x_fields.ttype == "many2one":
                                        if values.get(i):
                                            fetch_m2o = self.env[many2x_fields.relation].search(
                                                [('name', '=', values.get(i))])
                                            if fetch_m2o.id:
                                                pos_id.update({
                                                    technical_fields_name: fetch_m2o.id
                                                })
                                            else:
                                                raise ValidationError(
                                                    _('"%s" This custom field value "%s" not available in system') % (
                                                        many2x_fields.name, values.get(i)))
                                    if many2x_fields.ttype == "many2many":
                                        m2m_value_lst = []
                                        if values.get(i):
                                            if ';' in values.get(i):
                                                m2m_names = values.get(i).split(';')
                                                for name in m2m_names:
                                                    m2m_id = self.env[many2x_fields.relation].search(
                                                        [('name', '=', name)])
                                                    if not m2m_id:
                                                        raise ValidationError(
                                                            _('"%s" This custom field value "%s" not available in system') % (
                                                                many2x_fields.name, name))
                                                    m2m_value_lst.append(m2m_id.id)

                                            elif ',' in values.get(i):
                                                m2m_names = values.get(i).split(',')
                                                for name in m2m_names:
                                                    m2m_id = self.env[many2x_fields.relation].search(
                                                        [('name', '=', name)])
                                                    if not m2m_id:
                                                        raise ValidationError(
                                                            _('"%s" This custom field value "%s" not available in system') % (
                                                                many2x_fields.name, name))
                                                    m2m_value_lst.append(m2m_id.id)

                                            else:
                                                m2m_names = values.get(i).split(',')
                                                m2m_id = self.env[many2x_fields.relation].search(
                                                    [('name', 'in', m2m_names)])
                                                if not m2m_id:
                                                    raise ValidationError(
                                                        _('"%s" This custom field value "%s" not available in system') % (
                                                            many2x_fields.name, m2m_names))
                                                m2m_value_lst.append(m2m_id.id)
                                        pos_id.update({
                                            technical_fields_name: m2m_value_lst
                                        })
                                else:
                                    raise ValidationError(
                                        _('"%s" This custom field type is not many2one/many2many') % technical_fields_name)
                            else:
                                raise ValidationError(
                                    _('"%s" This m2x custom field is not available in system') % technical_fields_name)
                        else:
                            normal_fields = self.env['ir.model.fields'].search(
                                [('name', '=', normal_details), ('state', '=', 'manual'),
                                 ('model_id', '=', model_id.id)])
                            if normal_fields.id:
                                if normal_fields.ttype == 'boolean':
                                    pos_id.update({
                                        normal_details: values.get(i)
                                    })
                                elif normal_fields.ttype == 'char':
                                    pos_id.update({
                                        normal_details: values.get(i)
                                    })
                                elif normal_fields.ttype == 'float':
                                    if values.get(i) == '':
                                        float_value = 0.0
                                    else:
                                        float_value = float(values.get(i))
                                    pos_id.update({
                                        normal_details: float_value
                                    })
                                elif normal_fields.ttype == 'integer':
                                    if values.get(i) == '':
                                        int_value = 0
                                    else:
                                        int_value = int(values.get(i))
                                    pos_id.update({
                                        normal_details: int_value
                                    })
                                elif normal_fields.ttype == 'selection':
                                    pos_id.update({
                                        normal_details: values.get(i)
                                    })
                                elif normal_fields.ttype == 'text':
                                    pos_id.update({
                                        normal_details: values.get(i)
                                    })
                            else:
                                raise ValidationError(
                                    _('"%s" This custom field is not available in system') % normal_details)
            line = self.make_pos_line(values, pos_id)
            currency = pos_id.pricelist_id.currency_id
            pos_id.amount_tax = currency.round(
                sum(pos_id._amount_line_tax(line, pos_id.fiscal_position_id) for line in pos_id.lines))
            amount_untaxed = currency.round(sum(line.price_subtotal for line in pos_id.lines))
            pos_id.amount_total = pos_id.amount_tax + amount_untaxed
        return pos_id

    def make_pos_line(self, values, pos_id):
        pos_line_obj = self.env['pos.order.line']
        pos_obj = self.env['pos.order']

        if values.get('product_id'):
            product_name = values.get('product_id')
            if self.check_product(product_name) != None:
                product_id = self.check_product(product_name)

            if values.get('quantity'):
                quantity = values.get('quantity')

            if values.get('price_unit'):
                price_unit = values.get('price_unit')

            if values.get('discount'):
                discount = values.get('discount')

            line = pos_line_obj.create({
                'product_id': product_id.id,
                'full_product_name': product_id.name,
                'qty': quantity,
                'price_unit': price_unit,
                'discount': discount,
                'order_id': pos_id.id,
                'price_subtotal': 0.0,
                'price_subtotal_incl': 0.0,
            })
            line._onchange_amount_line_all()
        return values

    def validate_date(self, val):
        date = val.get('date')
        if val:
            try:
                date = date.replace('/', '-')
                c_in = datetime.datetime.strptime(date, '%d-%m-%Y %H:%M:%S')
            except Exception:
                raise ValidationError(_('Wrong Date Format. Date Should be in format YYYY-MM-DD.'))
            return c_in
        else:
            raise ValidationError(_('Date field is blank in sheet Please add the date.'))

    def import_pos_order(self):
        if self.import_option == 'csv':
            try:
                keys = ['name', 'session', 'date_order', 'salesperson', 'partner_id', 'product_id', 'quantity',
                        'price_unit', 'discount']
                csv_data = base64.b64decode(self.file_to_upload)
                data_file = io.StringIO(csv_data.decode("utf-8"))
                data_file.seek(0),
                file_reader = []
                csv_reader = csv.reader(data_file, delimiter=',')
                file_reader.extend(csv_reader)
            except Exception:
                raise ValidationError(_("Invalid file!"))
            values = {}
            lines = []
            for i in range(len(file_reader)):
                field = list(map(str, file_reader[i]))
                count = 1
                count_keys = len(keys)
                if len(field) > count_keys:
                    for new_fields in field:
                        if count > count_keys:
                            keys.append(new_fields)
                        count += 1
                values = dict(zip(keys, field))
                if values:
                    if i == 0:
                        continue
                    else:
                        res = self.make_pos(values)
        else:
            try:
                fp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
                fp.write(binascii.a2b_base64(self.file_to_upload))
                fp.seek(0)
                values = {}
                workbook = xlrd.open_workbook(fp.name)
                sheet = workbook.sheet_by_index(0)
            except Exception:
                raise ValidationError(_("Invalid file!"))

            lines = []
            for row_no in range(sheet.nrows):
                val = {}
                if row_no <= 0:
                    line_fields = map(lambda row: row.value.encode('utf-8'), sheet.row(row_no))
                else:
                    line = list(
                        map(lambda row: isinstance(row.value, bytes) and row.value.encode('utf-8') or str(row.value),
                            sheet.row(row_no)))
                    values = {
                        'name': line[0],
                        'session': line[1],
                        'date_order': line[2],
                        'salesperson': line[3],
                        'partner_id': line[4],
                        'product_id': line[5],
                        'quantity': line[6],
                        'price_unit': line[7],
                        'discount': line[8],
                    }
                    count = 0
                    for l_fields in line_fields:
                        if (count > 8):
                            values.update({l_fields: line[count]})
                        count += 1
                    res = self.make_pos(values)
