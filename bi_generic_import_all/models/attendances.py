# -*- coding: utf-8 -*-
# Part of BrowseInfo. See LICENSE file for full copyright and licensing details.

import tempfile
import binascii
import logging
import io
from odoo.tools import ustr
from odoo.exceptions import Warning ,ValidationError
from datetime import date, datetime,timedelta
from odoo import models, fields, api, exceptions, _
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
    import base64
except ImportError:
    _logger.debug('Cannot `import base64`.')
try:
    import xlrd
except ImportError:
    _logger.debug('Cannot `import xlrd`.')

class import_hr_attendance(models.Model):
    _inherit = "hr.attendance"

    is_import = fields.Boolean(string = " imported data" , default = False)

class import_attendance(models.TransientModel):
    _name = "import.attendance"
    _description = "Import Attendance"

    file = fields.Binary('File')
    file_opt = fields.Selection([('csv','CSV'),('excel','EXCEL')])

    
    def import_file(self):
        if self.file_opt == 'csv':
            try:
                keys = ['name','check_in','check_out']  
                csv_data = base64.b64decode(self.file)
                data_file = io.StringIO(csv_data.decode("utf-8"))
                data_file.seek(0)
                file_reader = []
                csv_reader = csv.reader(data_file, delimiter=',')
                file_reader.extend(csv_reader)
            except Exception:
                raise ValidationError(_("Please select an CSV/XLS file or You have selected invalid file"))
            values = {}
            for i in range(len(file_reader)):
                field = list(map(str, file_reader[i]))
                count = 1
                count_keys = len(keys)
                if len(field) > count_keys:
                    for new_fields in field:
                        if count > count_keys :
                            keys.append(new_fields)                
                        count+=1 
                values = dict(zip(keys, field))
                if values:
                    if i == 0:
                        continue
                    else:
                        res = self._create_timesheet(values)            
        else:
            try:
                fp = tempfile.NamedTemporaryFile(delete= False,suffix=".xlsx")
                fp.write(binascii.a2b_base64(self.file))
                fp.seek(0)
                values = {}
                workbook = xlrd.open_workbook(fp.name)
                sheet = workbook.sheet_by_index(0)
            except Exception:
                raise ValidationError(_("Please select an CSV/XLS file or You have selected invalid file"))
                            
            for row_no in range(sheet.nrows):
                if row_no <= 0:
                    line_fields = map(lambda row:row.value.encode('utf-8'), sheet.row(row_no))
                else:
                    line = list(map(lambda row:isinstance(row.value, bytes) and row.value.encode('utf-8') or str(row.value), sheet.row(row_no)))
                    
                    values.update( {'name':line[0],
                                    'check_in': line[1],
                                    'check_out': line[2],
                                    })
                   
                    count = 0
                    for l_fields in line_fields:
                        if(count > 2):
                            values.update({l_fields : line[count]})                        
                        count+=1 
                    res = self._create_timesheet(values)
            
            return res

    
    def _create_timesheet(self,values):
        emp_id = self._find_employee(values.get('name'))
        if not emp_id:
            raise ValidationError('Employee Not Found')
        if not values.get('check_in'):
            raise ValidationError('Please Provide Sign In Time')
        if not values.get('check_out'):
            check_in_time = self.make_att_date(values.get('check_in'))
            attendance = self.env['hr.attendance'].create({
                    'employee_id' :emp_id.id,
                    'check_in':check_in_time,
                    'is_import' : True
                })
            attendance._compute_worked_hours()
            main_list = values.keys()
            for i in main_list:
                model_id = self.env['ir.model'].search([('model','=','hr.attendance')])           
                if type(i) == bytes:
                    normal_details = i.decode('utf-8')
                else:
                    normal_details = i
                if normal_details.startswith('x_'):
                    any_special = self.check_splcharacter(normal_details)
                    if any_special:
                        split_fields_name = normal_details.split("@")
                        technical_fields_name = split_fields_name[0]
                        many2x_fields = self.env['ir.model.fields'].search([('name','=',technical_fields_name),('state','=','manual'),('model_id','=',model_id.id)])
                        if many2x_fields.id:
                            if many2x_fields.ttype in ['many2one','many2many']:
                                if many2x_fields.ttype =="many2one":
                                    if values.get(i):
                                        fetch_m2o = self.env[many2x_fields.relation].search([('name','=',values.get(i))])
                                        if fetch_m2o.id:
                                            attendance.update({
                                                technical_fields_name: fetch_m2o.id
                                                })
                                        else:
                                            raise ValidationError(_('"%s" This custom field value "%s" not available in system') % (many2x_fields.name , values.get(i)))
                                if many2x_fields.ttype =="many2many":
                                    m2m_value_lst = []
                                    if values.get(i):
                                        if ';' in values.get(i):
                                            m2m_names = values.get(i).split(';')
                                            for name in m2m_names:
                                                m2m_id = self.env[many2x_fields.relation].search([('name', '=', name)])
                                                if not m2m_id:
                                                    raise ValidationError(_('"%s" This custom field value "%s" not available in system') % (many2x_fields.name , name))
                                                m2m_value_lst.append(m2m_id.id)

                                        elif ',' in values.get(i):
                                            m2m_names = values.get(i).split(',')
                                            for name in m2m_names:
                                                m2m_id = self.env[many2x_fields.relation].search([('name', '=', name)])
                                                if not m2m_id:
                                                    raise ValidationError(_('"%s" This custom field value "%s" not available in system') % (many2x_fields.name , name))
                                                m2m_value_lst.append(m2m_id.id)

                                        else:
                                            m2m_names = values.get(i).split(',')
                                            m2m_id = self.env[many2x_fields.relation].search([('name', 'in', m2m_names)])
                                            if not m2m_id:
                                                raise ValidationError(_('"%s" This custom field value "%s" not available in system') % (many2x_fields.name , m2m_names))
                                            m2m_value_lst.append(m2m_id.id)
                                    attendance.update({
                                        technical_fields_name : m2m_value_lst
                                        })     
                            else:
                                raise ValidationError(_('"%s" This custom field type is not many2one/many2many') % technical_fields_name)                             
                        else:
                            raise ValidationError(_('"%s" This m2x custom field is not available in system') % technical_fields_name)
                    else:
                        normal_fields = self.env['ir.model.fields'].search([('name','=',normal_details),('state','=','manual'),('model_id','=',model_id.id)])
                        if normal_fields.id:
                            if normal_fields.ttype ==  'boolean':
                                attendance.update({
                                    normal_details : values.get(i)
                                    })
                            elif normal_fields.ttype == 'char':
                                attendance.update({
                                    normal_details : values.get(i)
                                    })                              
                            elif normal_fields.ttype == 'float':
                                if values.get(i) == '':
                                    float_value = 0.0
                                else:
                                    float_value = float(values.get(i)) 
                                attendance.update({
                                    normal_details : float_value
                                    })                              
                            elif normal_fields.ttype == 'integer':
                                if values.get(i) == '':
                                    int_value = 0
                                else:
                                    int_value = int(values.get(i)) 
                                attendance.update({
                                    normal_details : int_value
                                    })                            
                            elif normal_fields.ttype == 'selection':
                                attendance.update({
                                    normal_details : values.get(i)
                                    })                              
                            elif normal_fields.ttype == 'text':
                                attendance.update({
                                    normal_details : values.get(i)
                                    })                              
                        else:
                            raise ValidationError(_('"%s" This custom field is not available in system') % normal_details)            
        else:
            check_in_date = self.make_att_date(values.get('check_in'))
            check_out_date = self.make_att_date(values.get('check_out'))
            attendance = self.env['hr.attendance'].create({
                    'employee_id' :emp_id.id,
                    'check_in':check_in_date,
                    'check_out' : check_out_date,
                    'is_import' : True
                })
            attendance._compute_worked_hours()
            main_list = values.keys()
            for i in main_list:
                model_id = self.env['ir.model'].search([('model','=','hr.attendance')])           
                if type(i) == bytes:
                    normal_details = i.decode('utf-8')
                else:
                    normal_details = i
                if normal_details.startswith('x_'):
                    any_special = self.check_splcharacter(normal_details)
                    if any_special:
                        split_fields_name = normal_details.split("@")
                        technical_fields_name = split_fields_name[0]
                        many2x_fields = self.env['ir.model.fields'].search([('name','=',technical_fields_name),('state','=','manual'),('model_id','=',model_id.id)])
                        if many2x_fields.id:
                            if many2x_fields.ttype in ['many2one','many2many']:
                                if many2x_fields.ttype =="many2one":
                                    if values.get(i):
                                        fetch_m2o = self.env[many2x_fields.relation].search([('name','=',values.get(i))])
                                        if fetch_m2o.id:
                                            attendance.update({
                                                technical_fields_name: fetch_m2o.id
                                                })
                                        else:
                                            raise ValidationError(_('"%s" This custom field value "%s" not available in system') % (many2x_fields.name , values.get(i)))
                                if many2x_fields.ttype =="many2many":
                                    m2m_value_lst = []
                                    if values.get(i):
                                        if ';' in values.get(i):
                                            m2m_names = values.get(i).split(';')
                                            for name in m2m_names:
                                                m2m_id = self.env[many2x_fields.relation].search([('name', '=', name)])
                                                if not m2m_id:
                                                    raise ValidationError(_('"%s" This custom field value "%s" not available in system') % (many2x_fields.name , name))
                                                m2m_value_lst.append(m2m_id.id)

                                        elif ',' in values.get(i):
                                            m2m_names = values.get(i).split(',')
                                            for name in m2m_names:
                                                m2m_id = self.env[many2x_fields.relation].search([('name', '=', name)])
                                                if not m2m_id:
                                                    raise ValidationError(_('"%s" This custom field value "%s" not available in system') % (many2x_fields.name , name))
                                                m2m_value_lst.append(m2m_id.id)

                                        else:
                                            m2m_names = values.get(i).split(',')
                                            m2m_id = self.env[many2x_fields.relation].search([('name', 'in', m2m_names)])
                                            if not m2m_id:
                                                raise ValidationError(_('"%s" This custom field value "%s" not available in system') % (many2x_fields.name , m2m_names))
                                            m2m_value_lst.append(m2m_id.id)
                                    attendance.update({
                                        technical_fields_name : m2m_value_lst
                                        })     
                            else:
                                raise ValidationError(_('"%s" This custom field type is not many2one/many2many') % technical_fields_name)                             
                        else:
                            raise ValidationError(_('"%s" This m2x custom field is not available in system') % technical_fields_name)
                    else:
                        normal_fields = self.env['ir.model.fields'].search([('name','=',normal_details),('state','=','manual'),('model_id','=',model_id.id)])
                        if normal_fields.id:
                            if normal_fields.ttype ==  'boolean':
                                attendance.update({
                                    normal_details : values.get(i)
                                    })
                            elif normal_fields.ttype == 'char':
                                attendance.update({
                                    normal_details : values.get(i)
                                    })                              
                            elif normal_fields.ttype == 'float':
                                if values.get(i) == '':
                                    float_value = 0.0
                                else:
                                    float_value = float(values.get(i)) 
                                attendance.update({
                                    normal_details : float_value
                                    })                              
                            elif normal_fields.ttype == 'integer':
                                if values.get(i) == '':
                                    int_value = 0
                                else:
                                    int_value = int(values.get(i)) 
                                attendance.update({
                                    normal_details : int_value
                                    })                            
                            elif normal_fields.ttype == 'selection':
                                attendance.update({
                                    normal_details : values.get(i)
                                    })                              
                            elif normal_fields.ttype == 'text':
                                attendance.update({
                                    normal_details : values.get(i)
                                    })                              
                        else:
                            raise ValidationError(_('"%s" This custom field is not available in system') % normal_details)            
        return True

    def _find_employee(self,name):
        emp_id = self.env['hr.employee'].search([('name','=',name)])
        if emp_id:
            return emp_id
        else:
            raise ValidationError(_("Employee '%s' Not Found!") % ustr(name))

    def make_att_date(self, date):
        DATETIME_FORMAT = '%m/%d/%Y %H:%M:%S'
        if date:
            try:
                i_date = datetime.strptime(date, DATETIME_FORMAT)- timedelta(hours=5,minutes=30)
            except Exception:
                raise ValidationError(_('Wrong Date Format. Date Should be in format MM/DD/YYYY H:M:S.'))
            return i_date
        else:
            raise ValidationError(_('Date field is blank in sheet Please add the date.'))        



