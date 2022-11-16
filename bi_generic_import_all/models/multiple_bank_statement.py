# -*- coding: utf-8 -*-
# Part of BrowseInfo. See LICENSE file for full copyright and licensing details.


import io
import re
import tempfile
import binascii
import logging
import datetime
from odoo.exceptions import Warning, ValidationError
from odoo import models, fields, api, exceptions, _
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT, DEFAULT_SERVER_DATE_FORMAT

_logger = logging.getLogger(__name__)
from io import StringIO

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
try:
    import xlrd
    from xlrd import XLRDError
except ImportError:
    _logger.debug('Cannot `import xlrd`.')


class import_hr_attendance(models.Model):
    _inherit = "account.bank.statement"

    is_import = fields.Boolean(string=" imported data", default=False)


class AccountMultipleBankStatementWizard(models.TransientModel):
    _name = "account.multiple.bank.statement.wizard"
    _description = "Account Multiple Bank Statement Wizard"

    file = fields.Binary('File')
    file_opt = fields.Selection([('excel', 'Excel'), ('csv', 'CSV')])

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

    def date_computaion(self, date, workbook):
        if date != '':
            dte = date.split('-')
            if len(dte) == 3 and (len(dte[0]) == 4 and len(dte[1]) == 2 and len(dte[2]) == 2):
                date_string = date
            elif date:
                try:
                    a1 = int(float(date))
                    a1_as_datetime = datetime.datetime(*xlrd.xldate_as_tuple(a1, workbook.datemode))
                    date_string = a1_as_datetime.date().strftime('%Y-%m-%d')
                except:
                    raise ValidationError(_('Wrong Date Format. Date Should be in format YYYY-MM-DD.'))
            else:
                raise ValidationError(_('1Wrong Date Format. Date Should be in format YYYY-MM-DD.'))
        else:
            date_string = False
        return self.make_statement_date(date_string);

    def import_multiple_bank_statement(self):
        if self.file_opt == 'csv':
            try:
                keys = ['name', 'date', 'accounting_date', 'journal_id', 'line_date', 'ref', 'partner', 'memo',
                        'amount', 'currency']
                data = base64.b64decode(self.file)
                file_input = io.StringIO(data.decode("utf-8"))
                file_input.seek(0)
                reader_info = []
                csv_reader = csv.reader(file_input, delimiter=',')
                reader_info.extend(csv_reader)
            except Exception:
                raise ValidationError(_("Invalid file!"))
            values = {}
            for i in range(len(reader_info)):
                field = list(map(str, reader_info[i]))
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
                        if values.get('accounting_date') == '':
                            raise ValidationError('Please Provide Account Date Field Value')
                        if values.get('line_date') == '':
                            raise ValidationError('Please Provide Line Date Field Value')
                        if values.get('date') == '':
                            raise ValidationError('Please Provide Date Field Value')

                        res = self.create_bank_statement(values)

        elif self.file_opt == 'excel':
            try:
                fp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
                fp.write(binascii.a2b_base64(self.file))
                fp.seek(0)
                values = {}
                workbook = ''
                workbook = xlrd.open_workbook(fp.name)
                sheet = workbook.sheet_by_index(0)
            except Exception:
                raise ValidationError(_("Invalid file!"))

            for row_no in range(sheet.nrows):
                if row_no <= 0:
                    line_fields = list(map(lambda row: row.value.encode('utf-8'), sheet.row(row_no)))
                else:
                    line = list(
                        map(lambda row: isinstance(row.value, bytes) and row.value.encode('utf-8') or str(row.value),
                            sheet.row(row_no)))
                    if not line[1]:
                        raise ValidationError('Please Provide Date Field Value')

                    date_string = self.date_computaion(line[1], workbook)

                    if not line[2]:
                        raise ValidationError('Please Provide Account Date Field Value')

                    acc_date_string = self.date_computaion(line[2], workbook)

                    if not line[4]:
                        raise ValidationError('Please Provide Line Date Field Value')

                    line_date_string = self.date_computaion(line[4], workbook)

                    values.update({
                        'name': line[0],
                        'date': date_string,
                        'accounting_date': acc_date_string,
                        'journal_id': line[3],
                        'line_date': line_date_string,
                        'ref': line[5],
                        'partner': line[6],
                        'memo': line[7],
                        'amount': line[8],
                        'currency': line[9],
                    })
                    count = 0
                    for l_fields in line_fields:
                        if (count > 9):
                            values.update({l_fields: line[count]})
                        count += 1
                    res = self.create_bank_statement(values)
        else:
            raise ValidationError(_('Please Select File Type'))

        return res

    def make_statement_date(self, date):
        DATETIME_FORMAT = "%Y-%m-%d"
        if date:
            try:
                i_date = datetime.datetime.strptime(str(date), DATETIME_FORMAT).date()
            except Exception:
                raise ValidationError(_('2Wrong Date Format. Date Should be in format YYYY-MM-DD.'))
            return i_date
        else:
            raise ValidationError(_('Date field is blank in sheet Please add the date.'))

    def create_bank_statement(self, values):
        bank_statement_obj = self.env['account.bank.statement']
        journal_id = self._find_journal(values.get('journal_id'))
        journal = self.env['account.journal'].browse(journal_id)

        bank_recent_id = bank_statement_obj.search([('journal_id', '=', journal.id)], order="id desc", limit=1)
        bank_statement_ids = bank_statement_obj.search([('name', '=', values.get('name'))])
        balance_start = 0.0

        if bank_recent_id:
            new_ = values.get('date')
            new_date = None
            if isinstance(new_, str):
                new_date = self.make_statement_date(new_)
            else:
                new_date = values.get('date')

        if bank_statement_ids:
            if bank_statement_ids.journal_id.name == journal.name:
                b = self.create_bank_statement_lines(values, bank_statement_ids)
                bank_statement_ids.write({
                    'balance_end_real': bank_statement_ids.balance_end
                })
                return bank_statement_ids
            else:
                raise ValidationError(
                    _('Journal is different for "%s" .\n Please define same.') % values.get('journal_id'))
        else:
            if not values.get('date'):
                raise ValidationError(_('Please Provide Date Field Value for Bank Statement.'))
            if bank_recent_id:
                balance_start = bank_recent_id.balance_end

            i_date = self.make_statement_date(values.get('date'))
            vals = {
                'name': values.get('name'),
                'journal_id': journal_id,
                'balance_start': balance_start,
                'is_import': True,
                'date': i_date,
            }
            main_list = values.keys()
            for i in main_list:
                model_id = self.env['ir.model'].search([('model', '=', 'account.bank.statement')])
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
                                            vals.update({
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
                                    vals.update({
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
                            [('name', '=', normal_details), ('state', '=', 'manual'), ('model_id', '=', model_id.id)])
                        if normal_fields.id:
                            if normal_fields.ttype == 'boolean':
                                vals.update({
                                    normal_details: values.get(i)
                                })
                            elif normal_fields.ttype == 'char':
                                vals.update({
                                    normal_details: values.get(i)
                                })
                            elif normal_fields.ttype == 'float':
                                if values.get(i) == '':
                                    float_value = 0.0
                                else:
                                    float_value = float(values.get(i))
                                vals.update({
                                    normal_details: float_value
                                })
                            elif normal_fields.ttype == 'integer':
                                if values.get(i) == '':
                                    int_value = 0
                                else:
                                    int_value = int(values.get(i))
                                vals.update({
                                    normal_details: int_value
                                })
                            elif normal_fields.ttype == 'selection':
                                vals.update({
                                    normal_details: values.get(i)
                                })
                            elif normal_fields.ttype == 'text':
                                vals.update({
                                    normal_details: values.get(i)
                                })
                        else:
                            raise ValidationError(
                                _('"%s" This custom field is not available in system') % normal_details)

            bank_statement_id = bank_statement_obj.create(vals)

            self.create_bank_statement_lines(values, bank_statement_id)

        return bank_statement_id

    def create_bank_statement_lines(self, values, bank_statement_id):
        account_bank_statement_line_obj = self.env['account.bank.statement.line']
        partner_id = self._find_partner(values.get('partner'))
        if values.get('currency') != '':
            currency_id = self._find_currency(values.get('currency'))
        else:
            raise ValidationError(_('Please Provide Currency Value for Bank Statement.'))

        if not values.get('memo'):
            raise ValidationError(_('Please Provide Memo Field Value'))

        i_date = self.make_statement_date(values.get('line_date'))
        value = {
            'date': i_date,
            'payment_ref': values.get('ref'),
            'partner_id': partner_id or False,
            'name': values.get('memo'),
            'amount': values.get('amount'),
            'currency_id': currency_id,
            'statement_id': bank_statement_id.id,
        }
        bank_statement_lines = account_bank_statement_line_obj.create(value)
        return True

    def _find_partner(self, name):
        partner_id = self.env['res.partner'].search([('name', '=', name)])
        if partner_id:
            return partner_id.id
        else:
            return

    def _find_currency(self, currency):
        currency_id = self.env['res.currency'].search([('name', '=', currency)])
        if currency_id:
            return currency_id.id
        else:
            raise ValidationError(_(' "%s" Currency are not available.') % currency)

    def _find_journal(self, name):
        journal_id = self.env['account.journal'].search([('name', '=', name)])
        if journal_id:
            return journal_id.id
        else:
            raise ValidationError(_(' "%s" Journal is not available.') % (name))


