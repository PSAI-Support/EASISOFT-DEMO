# -*- coding: utf-8 -*-
# Part of BrowseInfo. See LICENSE file for full copyright and licensing details.

import time
import tempfile
import binascii
import xlrd
import io
import re
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT, DEFAULT_SERVER_DATE_FORMAT
from datetime import date, datetime
from odoo.exceptions import Warning, ValidationError, UserError
from odoo import models, fields, exceptions, api, _

import logging

_logger = logging.getLogger(__name__)

try:
    import csv
except ImportError:
    _logger.debug('Cannot `import csv`.')
try:
    import xlwt
except ImportError:
    _logger.debug('Cannot `import xlwt`.')
try:
    import cStringIO
except ImportError:
    _logger.debug('Cannot `import cStringIO`.')
try:
    import base64
except ImportError:
    _logger.debug('Cannot `import base64`.')

TYPE2JOURNAL = {
    'out_invoice': 'sale',
    'in_invoice': 'purchase',
    'out_refund': 'sale',
    'in_refund': 'purchase',
}


class AccountMove(models.Model):
    _inherit = "account.move"

    custom_seq = fields.Boolean('Custom Sequence')
    system_seq = fields.Boolean('System Sequence')
    invoice_name = fields.Char('Invocie Name')
    custom_name = fields.Char('Custom Name')
    is_import = fields.Boolean("import records", default=False)


class gen_inv(models.TransientModel):
    _name = "gen.invoice"
    _description = "Generic Invoice"

    file = fields.Binary('File')
    account_opt = fields.Selection(
        [('default', 'Use Account From Configuration product/Property'), ('custom', 'Use Account From Excel/CSV')],
        string='Account Option', required=True, default='default')
    type = fields.Selection([('in', 'Customer'), ('out', 'Supplier'), ('cus_credit_note', 'Customer Credit Note'),
                             ('ven_credit_note', 'Vendor Credit Note')], string='Type', required=True, default='in')
    sequence_opt = fields.Selection(
        [('custom', 'Use Excel/CSV Sequence Number'), ('system', 'Use System Default Sequence Number')],
        string='Sequence Option', default='custom')
    import_option = fields.Selection([('csv', 'CSV File'), ('xls', 'XLS File')], string='Select', default='csv')
    stage = fields.Selection(
        [('draft', 'Import Draft Invoice'), ('confirm', 'Validate Invoice Automatically With Import')],
        string="Invoice Stage Option", default='draft')
    import_prod_option = fields.Selection([('name', 'Name'), ('code', 'Code'), ('barcode', 'Barcode')],
                                          string='Import Product By ', default='name')

    def check_splcharacter(self, test):
        # Make own character set and pass 
        # this as argument in compile method

        string_check = re.compile('@')

        # Pass the string in search 
        # method of regex object.
        if (string_check.search(str(test)) == None):
            return False
        else:
            return True

    def make_invoice(self, values):
        invoice_obj = self.env['account.move']
        if self.sequence_opt == "custom":
            if self.type == "in":
                invoice_search = invoice_obj.search([
                    ('custom_name', '=', values.get('invoice')),
                    ('move_type', '=', 'out_invoice')
                ])
            elif self.type == 'out':
                invoice_search = invoice_obj.search([
                    ('custom_name', '=', values.get('invoice')),
                    ('move_type', '=', 'in_invoice')
                ])
            elif self.type == 'cus_credit_note':
                invoice_search = invoice_obj.search([
                    ('custom_name', '=', values.get('invoice')),
                    ('move_type', '=', 'out_refund')
                ])
            else:
                invoice_search = invoice_obj.search([
                    ('custom_name', '=', values.get('invoice')),
                    ('move_type', '=', 'in_refund')
                ])
        else:
            if self.type == "in":
                invoice_search = invoice_obj.search([
                    ('invoice_name', '=', values.get('invoice')),
                    ('move_type', '=', 'out_invoice')
                ])
            elif self.type == 'out':
                invoice_search = invoice_obj.search([
                    ('invoice_name', '=', values.get('invoice')),
                    ('move_type', '=', 'in_invoice')
                ])
            elif self.type == 'cus_credit_note':
                invoice_search = invoice_obj.search([
                    ('invoice_name', '=', values.get('invoice')),
                    ('move_type', '=', 'out_refund')
                ])
            else:
                invoice_search = invoice_obj.search([
                    ('invoice_name', '=', values.get('invoice')),
                    ('move_type', '=', 'in_refund')
                ])
        if invoice_search:
            if invoice_search.partner_id.name == values.get('customer'):
                if invoice_search.currency_id.name == values.get('currency'):
                    if invoice_search.user_id.name == values.get('salesperson'):
                        self.make_invoice_line(values, invoice_search)
                        return invoice_search
                    else:
                        raise ValidationError(_('User(Salesperson) is different for "%s" .\n Please define same.') % (
                            values.get('invoice')))
                else:
                    raise ValidationError(
                        _('Currency is different for "%s" .\n Please define same.') % (values.get('invoice')))
            else:
                raise ValidationError(
                    _('Customer name is different for "%s" .\n Please define same.') % (values.get('invoice')))

        else:
            partner_id = self.find_partner(values.get('customer'))
            currency_id = self.find_currency(values.get('currency'))
            salesperson_id = self.find_sales_person(values.get('salesperson'))
            inv_date = self.find_invoice_date(values.get('date'))

            if self.type == "in":
                type_inv = "out_invoice"
                if partner_id.property_account_receivable_id:
                    account_id = partner_id.property_account_receivable_id
                else:
                    account_search = self.env['ir.property'].search([('name', '=', 'property_account_receivable_id')])
                    account_id = account_search.value_reference
                    if not account_id:
                        raise UserError(_('Please define Customer account.'))
                    account_id = account_id.split(",")[1]
                    account_id = self.env['account.account'].browse(account_id)

            elif self.type == "out":
                type_inv = "in_invoice"
                if partner_id.property_account_payable_id:
                    account_id = partner_id.property_account_payable_id
                else:
                    account_search = self.env['ir.property'].search([('name', '=', 'property_account_payable_id')])
                    account_id = account_search.value_reference
                    if not account_id:
                        raise UserError(_('Please define Vendor account.'))
                    account_id = account_id.split(",")[1]
                    account_id = self.env['account.account'].browse(account_id)

            elif self.type == "cus_credit_note":
                type_inv = "out_refund"
                if partner_id.property_account_receivable_id:
                    account_id = partner_id.property_account_receivable_id
                else:
                    account_search = self.env['ir.property'].search([('name', '=', 'property_account_receivable_id')])
                    account_id = account_search.value_reference
                    if not account_id:
                        raise UserError(_('Please define Customer account.'))
                    account_id = account_id.split(",")[1]
                    account_id = self.env['account.account'].browse(account_id)
            else:
                type_inv = "in_refund"
                if partner_id.property_account_payable_id:
                    account_id = partner_id.property_account_payable_id
                else:
                    account_search = self.env['ir.property'].search([('name', '=', 'property_account_payable_id')])
                    account_id = account_search.value_reference
                    if not account_id:
                        raise UserError(_('Please define Vendor account.'))
                    account_id = account_id.split(",")[1]
                    account_id = self.env['account.account'].browse(account_id)

            if type_inv == "in_invoice":
                journal_type = 'purchase'
            elif type_inv == "out_invoice":
                journal_type = 'sale'
            elif type_inv == "out_refund":
                journal_type = 'sale'
            else:
                journal_type = 'purchase'

            if self._context.get('default_journal_id', False):
                journal = self.env['account.journal'].browse(self._context.get('default_journal_id'))
            inv_type = journal_type
            inv_types = inv_type if isinstance(inv_type, list) else [inv_type]
            company_id = self._context.get('company_id', self.env.user.company_id.id)
            domain = [
                ('type', 'in', [journal_type]),
                ('company_id', '=', company_id),
            ]
            journal = self.env['account.journal'].search(domain, limit=1)

            name = values.get('invoice')

            inv_id = invoice_obj.create({
                'partner_id': partner_id.id,
                'currency_id': currency_id.id,
                'user_id': salesperson_id.id,
                'name': name,
                'custom_name': name,
                'is_import': True,
                'custom_seq': True if values.get('seq_opt') == 'custom' else False,
                'system_seq': True if values.get('seq_opt') == 'system' else False,
                'move_type': type_inv,
                'invoice_date': inv_date,
                'journal_id': journal.id,
                'invoice_name': values.get('invoice'),

            })
            main_list = values.keys()

            for i in main_list:
                model_id = self.env['ir.model'].search([('model', '=', 'account.move')])

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
                            [('name', '=', technical_fields_name), ('model_id', '=', model_id.id)])
                        if many2x_fields.id:
                            if many2x_fields.ttype in ['many2one', 'many2many']:
                                if many2x_fields.ttype == "many2one":
                                    if values.get(i):
                                        fetch_m2o = self.env[many2x_fields.relation].search(
                                            [('name', '=', values.get(i))])
                                        if fetch_m2o.id:
                                            inv_id.update({
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
                                                m2m_id = self.env[many2x_fields.relation].search([('name', '=', name)])
                                                if not m2m_id:
                                                    raise ValidationError(
                                                        _('"%s" This custom field value "%s" not available in system') % (
                                                            many2x_fields.name, name))
                                                m2m_value_lst.append(m2m_id.id)

                                        elif ',' in values.get(i):
                                            m2m_names = values.get(i).split(',')
                                            for name in m2m_names:
                                                m2m_id = self.env[many2x_fields.relation].search([('name', '=', name)])
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
                                    inv_id.update({
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
                            [('name', '=', normal_details), ('model_id', '=', model_id.id)])
                        if normal_fields.id:
                            if normal_fields.ttype == 'boolean':
                                inv_id.update({
                                    normal_details: values.get(i)
                                })
                            elif normal_fields.ttype == 'char':
                                inv_id.update({
                                    normal_details: values.get(i)
                                })
                            elif normal_fields.ttype == 'float':
                                if values.get(i) == '':
                                    float_value = 0.0
                                else:
                                    float_value = float(values.get(i))
                                inv_id.update({
                                    normal_details: float_value
                                })
                            elif normal_fields.ttype == 'integer':
                                if values.get(i) == '':
                                    int_value = 0
                                else:
                                    try:
                                        int_value = int(float(values.get(i)))
                                    except:
                                        raise ValidationError(_("Wrong value %s for Integer" % values.get(i)))
                                inv_id.update({
                                    normal_details: int_value
                                })
                            elif normal_fields.ttype == 'selection':
                                inv_id.update({
                                    normal_details: values.get(i)
                                })
                            elif normal_fields.ttype == 'text':
                                inv_id.update({
                                    normal_details: values.get(i)
                                })
                        else:
                            raise ValidationError(
                                _('"%s" This custom field is not available in system') % normal_details)
            self.make_invoice_line(values, inv_id)
            if values.get('seq_opt') == 'system':
                inv_id.update({'name': '/'})
            return inv_id

    def make_invoice_line(self, values, inv_id):
        product_obj = self.env['product.product']
        invoice_line_obj = self.env['account.move.line']

        if self.import_prod_option == 'barcode':
            product_search = product_obj.search([('barcode', '=', values['product'])], limit=1)
        elif self.import_prod_option == 'code':
            product_search = product_obj.search([('default_code', '=', values['product'])], limit=1)
        else:
            product_search = product_obj.search([('name', '=', values['product'])], limit=1)

        product_uom = self.env['uom.uom'].search([('name', '=', values.get('uom'))])
        if not product_uom:
            raise ValidationError(_(' "%s" Product UOM category is not available.') % values.get('uom'))

        if product_search:
            product_id = product_search
        else:
            if self.import_prod_option == 'name':
                if values.get('price'):
                    list_price = float(values.get('price'))
                else:
                    list_price = False
                product_id = product_obj.create({
                    'name': values.get('product'),
                    'list_price': list_price,
                    'uom_id': product_uom.id,
                })
            else:
                raise ValidationError(
                    _('%s product is not found" .\n If you want to create product then first select Import Product By Name option .') % values.get(
                        'product'))

        tax_ids = []
        if inv_id.move_type == 'out_invoice':
            if values.get('tax'):
                if ';' in values.get('tax'):
                    tax_names = values.get('tax').split(';')
                    for name in tax_names:
                        tax = self.env['account.tax'].search([('name', '=', name), ('type_tax_use', '=', 'sale')])
                        if not tax:
                            raise ValidationError(_('"%s" Tax not in your system') % name)
                        tax_ids.append(tax.id)

                elif ',' in values.get('tax'):
                    tax_names = values.get('tax').split(',')
                    for name in tax_names:
                        tax = self.env['account.tax'].search([('name', '=', name), ('type_tax_use', '=', 'sale')])
                        if not tax:
                            raise ValidationError(_('"%s" Tax not in your system') % name)
                        tax_ids.append(tax.id)
                else:
                    tax_names = values.get('tax').split(',')
                    tax = self.env['account.tax'].search([('name', '=', tax_names), ('type_tax_use', '=', 'sale')])
                    if not tax:
                        raise ValidationError(_('"%s" Tax not in your system') % tax_names)
                    tax_ids.append(tax.id)
        elif inv_id.move_type == 'in_invoice':
            if values.get('tax'):
                if ';' in values.get('tax'):
                    tax_names = values.get('tax').split(';')
                    for name in tax_names:
                        tax = self.env['account.tax'].search([('name', '=', name), ('type_tax_use', '=', 'purchase')])
                        if not tax:
                            raise ValidationError(_('"%s" Tax not in your system') % name)
                        tax_ids.append(tax.id)

                elif ',' in values.get('tax'):
                    tax_names = values.get('tax').split(',')
                    for name in tax_names:
                        tax = self.env['account.tax'].search([('name', '=', name), ('type_tax_use', '=', 'purchase')])
                        if not tax:
                            raise ValidationError(_('"%s" Tax not in your system') % name)
                        tax_ids.append(tax.id)
                else:
                    tax_names = values.get('tax').split(',')
                    tax = self.env['account.tax'].search([('name', '=', tax_names), ('type_tax_use', '=', 'purchase')])
                    if not tax:
                        raise ValidationError(_('"%s" Tax not in your system') % tax_names)
                    tax_ids.append(tax.id)
        elif inv_id.move_type == 'out_refund':
            if values.get('tax'):
                if ';' in values.get('tax'):
                    tax_names = values.get('tax').split(';')
                    for name in tax_names:
                        tax = self.env['account.tax'].search([('name', '=', name), ('type_tax_use', '=', 'sale')])
                        if not tax:
                            raise ValidationError(_('"%s" Tax not in your system') % name)
                        tax_ids.append(tax.id)

                elif ',' in values.get('tax'):
                    tax_names = values.get('tax').split(',')
                    for name in tax_names:
                        tax = self.env['account.tax'].search([('name', '=', name), ('type_tax_use', '=', 'sale')])
                        if not tax:
                            raise ValidationError(_('"%s" Tax not in your system') % name)
                        tax_ids.append(tax.id)
                else:
                    tax_names = values.get('tax').split(',')
                    tax = self.env['account.tax'].search([('name', '=', tax_names), ('type_tax_use', '=', 'sale')])
                    if not tax:
                        raise ValidationError(_('"%s" Tax not in your system') % tax_names)
                    tax_ids.append(tax.id)
        else:
            if values.get('tax'):
                if ';' in values.get('tax'):
                    tax_names = values.get('tax').split(';')
                    for name in tax_names:
                        tax = self.env['account.tax'].search([('name', '=', name), ('type_tax_use', '=', 'purchase')])
                        if not tax:
                            raise ValidationError(_('"%s" Tax not in your system') % name)
                        tax_ids.append(tax.id)

                elif ',' in values.get('tax'):
                    tax_names = values.get('tax').split(',')
                    for name in tax_names:
                        tax = self.env['account.tax'].search([('name', '=', name), ('type_tax_use', '=', 'purchase')])
                        if not tax:
                            raise ValidationError(_('"%s" Tax not in your system') % name)
                        tax_ids.append(tax.id)
                else:
                    tax_names = values.get('tax').split(',')
                    tax = self.env['account.tax'].search([('name', '=', tax_names), ('type_tax_use', '=', 'purchase')])
                    if not tax:
                        raise ValidationError(_('"%s" Tax not in your system') % tax_names)
                    tax_ids.append(tax.id)

        if self.account_opt == 'default':
            if inv_id.move_type == 'out_invoice':
                if product_id.property_account_income_id:
                    account = product_id.property_account_income_id
                elif product_id.categ_id.property_account_income_categ_id:
                    account = product_id.categ_id.property_account_income_categ_id
                else:
                    account_search = self.env['ir.property'].search([('name', '=', 'property_account_income_categ_id')])
                    account = account_search.value_reference
                    account = account.split(",")[1]
                    account = self.env['account.account'].browse(account)
            if inv_id.move_type == 'in_invoice':
                if product_id.property_account_expense_id:
                    account = product_id.property_account_expense_id
                elif product_id.categ_id.property_account_expense_categ_id:
                    account = product_id.categ_id.property_account_expense_categ_id
                else:
                    account_search = self.env['ir.property'].search(
                        [('name', '=', 'property_account_expense_categ_id')])
                    account = account_search.value_reference
                    account = account.split(",")[1]
                    account = self.env['account.account'].browse(account)

            if inv_id.move_type == 'out_refund':
                if product_id.property_account_income_id:
                    account = product_id.property_account_income_id
                elif product_id.categ_id.property_account_income_categ_id:
                    account = product_id.categ_id.property_account_income_categ_id
                else:
                    account_search = self.env['ir.property'].search([('name', '=', 'property_account_income_categ_id')])
                    account = account_search.value_reference
                    account = account.split(",")[1]
                    account = self.env['account.account'].browse(account)
            if inv_id.move_type == 'in_refund':
                if product_id.property_account_expense_id:
                    account = product_id.property_account_expense_id
                elif product_id.categ_id.property_account_expense_categ_id:
                    account = product_id.categ_id.property_account_expense_categ_id
                else:
                    account_search = self.env['ir.property'].search(
                        [('name', '=', 'property_account_expense_categ_id')])
                    account = account_search.value_reference
                    account = account.split(",")[1]
                    account = self.env['account.account'].browse(account)

        else:
            if values.get('account') == '':
                raise ValidationError(_(' You can not left blank account field if you select Excel/CSV Account Option'))
            else:
                if self.import_option == 'csv':
                    account_id = self.env['account.account'].search([('code', '=', values.get('account'))])
                else:
                    acc = values.get('account').split('.')
                    account_id = self.env['account.account'].search([('code', '=', acc[0])])
                if account_id:
                    account = account_id
                else:
                    raise ValidationError(_(' "%s" Account is not available.') % values.get('account'))
        if values.get('price'):
            price_unit = float(values.get('price'))
        else:
            price_unit = False
        if values.get('quantity'):
            quantity = float(values.get('quantity'))
        else:
            quantity = False
        vals = {
            'product_id' : product_id.id,
            'quantity' : quantity,
            'price_unit' : price_unit,
            'name' : values.get('description'),
            'account_id' : account.id,
            'product_uom_id' : product_uom.id,
            'discount':values.get('DISCOUNT')

        }
        if tax_ids:
            vals.update({'tax_ids': ([(6, 0, tax_ids)])})
        inv_id.write({'invoice_line_ids': ([(0, 0, vals)])})

        return True

    def find_currency(self, name):
        currency_obj = self.env['res.currency']
        currency_search = currency_obj.search([('name', '=', name)])
        if currency_search:
            return currency_search
        else:
            raise ValidationError(_(' "%s" Currency are not available.') % name)

    def find_sales_person(self, name):
        sals_person_obj = self.env['res.users']
        partner_search = sals_person_obj.search([('name', '=', name)])
        if partner_search:
            return partner_search[0]
        else:
            raise ValidationError(_('Not Valid Salesperson Name "%s"') % name)

    def find_partner(self, name):
        partner_obj = self.env['res.partner']
        partner_search = partner_obj.search([('name', '=', name)])
        if partner_search:
            return partner_search[0]
        else:
            partner_id = partner_obj.create({
                'name': name})
            return partner_id

    def find_invoice_date(self, date):
        DATETIME_FORMAT = "%Y-%m-%d"
        if date:
            try:
                i_date = datetime.strptime(date, DATETIME_FORMAT).date()
            except Exception:
                raise ValidationError(_('Wrong Date Format. Date Should be in format YYYY-MM-DD.'))
            return i_date
        else:
            raise ValidationError(_('Date field is blank in sheet Please add the date.'))

    def import_csv(self):
        """Load Inventory data from the CSV file."""
        if self.import_option == 'csv':
            try:
                keys = ['invoice', 'customer', 'currency', 'product', 'account', 'quantity', 'uom', 'description',
                        'price', 'salesperson', 'tax', 'date', 'disc']
                csv_data = base64.b64decode(self.file)
                data_file = io.StringIO(csv_data.decode("utf-8"))
                data_file.seek(0)
                file_reader = []
                csv_reader = csv.reader(data_file, delimiter=',')
                file_reader.extend(csv_reader)
            except Exception:
                raise ValidationError(_("Invalid file!"))
            values = {}
            invoice_ids = []
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
                        values.update({'seq_opt': self.sequence_opt})
                        res = self.make_invoice(values)
                        invoice_ids.append(res)
            if self.stage == 'confirm':
                for res in invoice_ids:
                    if res.state in ['draft']:
                        res.action_post()

        else:
            try:
                fp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
                fp.write(binascii.a2b_base64(self.file))
                fp.seek(0)
                values = {}
                invoice_ids = []
                workbook = xlrd.open_workbook(fp.name)
                sheet = workbook.sheet_by_index(0)
            except Exception:
                raise ValidationError(_("Invalid file!"))

            for row_no in range(sheet.nrows):
                val = {}
                if row_no <= 0:
                    line_fields = map(lambda row: row.value.encode('utf-8'), sheet.row(row_no))
                else:
                    line = list(
                        map(lambda row: isinstance(row.value, bytes) and row.value.encode('utf-8') or str(row.value),
                            sheet.row(row_no)))
                    if line[11] != '':
                        if line[11].split('/'):
                            if len(line[11].split('/')) > 1:
                                raise ValidationError(_('Wrong Date Format. Date Should be in format YYYY-MM-DD.'))
                            if len(line[11]) > 8 or len(line[11]) < 5:
                                raise ValidationError(_('Wrong Date Format. Date Should be in format YYYY-MM-DD.'))
                    if line[11]:
                        a1 = int(float(line[11]))
                        a1_as_datetime = datetime(*xlrd.xldate_as_tuple(a1, workbook.datemode))
                        date_string = a1_as_datetime.date().strftime('%Y-%m-%d')
                    else:
                        date_string = False
                    values.update({'invoice': line[0],
                                   'customer': line[1],
                                   'currency': line[2],
                                   'product': line[3].split('.')[0],
                                   'account': line[4],
                                   'quantity': line[5],
                                   'uom': line[6],
                                   'description': line[7],
                                   'price': line[8],
                                   'salesperson': line[9],
                                   'tax': line[10],
                                   'date': date_string,
                                   'seq_opt': self.sequence_opt,
                                   'disc': line[12]
                                   })
                    count = 0
                    for l_fields in line_fields:
                        if (count > 12):
                            values.update({l_fields: line[count]})
                        count += 1

                    res = self.make_invoice(values)
                    invoice_ids.append(res)

            if self.stage == 'confirm':
                for res in invoice_ids:
                    if res.state in ['draft']:
                        res.action_post()

            return res
