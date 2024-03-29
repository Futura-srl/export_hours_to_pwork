from odoo import api, fields, models, http, _, Command
import logging, requests, json, pytz
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from odoo.exceptions import UserError, ValidationError
from itertools import groupby
from datetime import datetime as dt


_logger = logging.getLogger(__name__)
now = datetime.now()


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    datetime_start = fields.Datetime()
    datetime_stop = fields.Datetime()
    gtms_id = fields.Many2one('gtms.trip', readonly=True)
    validated_status = fields.Selection([('draft','Draft'),('validated','Validated'),('processing','Processing'),('done','On Pwork'),('error','Error')])
    error_txt = fields.Text(string='Response')
    processed = fields.Boolean(default=False, readonly=True)
    error = fields.Boolean(default=False, readonly=True)
    pwork = fields.Boolean(default=False, readonly=True)
    unit_amount = fields.Float(string="Hours Spent")

    @api.model
    def create(self,values):
        res = super(AccountAnalyticLine, self).create(values)
        if res.datetime_stop != False and res.datetime_start != False:
            work_time = res.datetime_stop - res.datetime_start
            working_seconds = work_time.total_seconds() / 3600.0
            res.write({'unit_amount': working_seconds})
            

    @api.onchange('datetime_start', 'datetime_stop')
    def _compute_unit_amount(self):
        for record in self:
            if record.datetime_stop != False and record.datetime_start != False:
                _logger.info(record.unit_amount)
                work_time = record.datetime_stop - record.datetime_start
                working_seconds = work_time.total_seconds() / 3600.0
                record.unit_amount = working_seconds
                _logger.info(record.unit_amount)


    @api.depends('validated', 'processed','error_txt', 'pwork')
    def _compute_validated_status(self):
        for line in self:
            if line.error == True:
                line.validated_status = 'error'
            elif line.validated and line.processed == False and line.error_txt == False:
                line.validated_status = 'validated'
            elif line.validated and line.processed == True and line.error_txt == False:
                line.validated_status = 'processing'
            elif line.validated and line.processed == True and line.pwork == True and line.error_txt != False:
                line.validated_status = 'done'
            else:
                line.validated_status = 'draft'

    def processing_to_pwork(self):
        for record in self:
            check_trip = self.check_remnants_trips(record.employee_id.id, record.date)
            if check_trip == True:
                if record.validated_status == 'validated':
                    record.processed = True
                else:
                    raise ValidationError(_(f"Il timesheet con id {record.id} deve essere prima sullo stato 'Validato'"))
        self.upload_to_pwork_table()

    def upload_to_pwork(self):
        for record in self:
            if record.validated_status == 'processing' or record.validated_status == 'error':
                data_e = record.datetime_start.strftime("%d/%m/%Y")
                ore_e = record.datetime_start.strftime("%H")
                minuti_e = record.datetime_start.strftime("%M")
                secondi_e = record.datetime_start.strftime("%S")
                data_u = record.datetime_stop.strftime("%d/%m/%Y")
                ore_u = record.datetime_stop.strftime("%H")
                minuti_u = record.datetime_stop.strftime("%M")
                secondi_u = record.datetime_stop.strftime("%S")
                
                # Recupero il badge del dipendente
                # badges = self.env['hr.badgespwork'].search_read([('active', '=', True), ('hr_id', '=', record.employee_id.id)],limit=1)
                # for badge in badges:
                #     _logger.info("Badge")
                # _logger.info(f"Stampo badge {badge['name']}")
                _logger.info(f"Stampo data_e {data_e}")
                _logger.info(f"Stampo ore_e {ore_e}")
                _logger.info(f"Stampo minuti_e {minuti_e}")
                _logger.info(f"Stampo secondi_e {secondi_e}")
                _logger.info(f"Stampo data_u {data_u}")
                _logger.info(f"Stampo ore_u {ore_u}")
                _logger.info(f"Stampo minuti_u {minuti_u}")
                _logger.info(f"Stampo secondi_u {secondi_u}")
                badge = '9999999991'
                response, element = self.send_timesheet(badge,data_e,ore_e,minuti_e,secondi_e,data_u,ore_u,minuti_u,secondi_u)
                self.pwork = response
                self.error_txt = element

    def check_remnants_trips(self, employee_id, date):
        for record in self:
        # Cerco se vi sono viaggi con il dipendente e data precisa ancora non nello stato "checked" o "cancel", nel caso mostro un avviso che non è possibile esportare le ore su Pwork in quanto vi sono ancora viaggi da gestire per quel dipendente.
            # employee_id = record.employee_id.id
            driver_id = self.env['hr.employee'].search_read([('id', '=', employee_id)], ['address_home_id'])[0]['address_home_id'][0]
            # date = record.date
            trips_open = self.env['gtms.trip'].search_read([('state', 'not in', ['checked', 'cancel']), ('current_driver_id.id', '=', driver_id), ('competence_date', '=', date)], ['id','competence_date','current_driver_id','state','name'])
            _logger.info("XXXXXXXXXXXXXXXXXX")
            _logger.info(trips_open)
            list_trips_open = ''
            if trips_open != []:
                for trip_open in trips_open:
                    list_trips_open += f"Viaggio {trip_open['name']} con id {trip_open['id']} é sullo stato {trip_open['state']}" + "\n"
                raise ValidationError(_(f"Il dipendente {record.employee_id.name} con id {record.employee_id.id} ha uno o più viaggi ancora in stati diversi da 'checked' o 'cancel' in data {record.date.strftime('%d/%m/%Y')}:\n{list_trips_open}"))
            else:
                return True
        
    def upload_to_pwork_table(self):
        shifts_data = []
        shifts_data_unique = []
        for record in self:
            # Prendo tutti i datetime e li inserisco in un array
            shifts_data.append((record.datetime_start, record.datetime_stop, record.employee_id, record.id))
    
        # Ordina i dati dei turni per dipendente, data (giorno) di inizio e ora di inizio
        shifts_data.sort(key=lambda x: (x[0].date()))
        for record in shifts_data:
            shifts_data_unique.append(record[2].id)

        shifts_data_unique = list(set(shifts_data_unique))
        _logger.info(shifts_data_unique)
        for single_employee in shifts_data_unique:
            
            combined_shifts = []
            combined_analytics = []
            # for single_employee_id in 
            for (employee), group in groupby(shifts_data, key=lambda x: x[2].name):
                # Itera attraverso i turni del gruppo
                for start, end, employee_id, analytic_id in group:
                    _logger.info(employee_id.id)
                    _logger.info(single_employee)
                    if single_employee != employee_id.id:
                        continue
                    # Controlla se c'è già un turno combinato
                    if combined_shifts:
                        # Ottieni l'ultimo turno combinato
                        last_combined_start, last_combined_end, last_combined_employee, last_combined_analytic = combined_shifts[-1]
        
                        # Se l'ultimo turno combinato finisce quando inizia il turno attuale, unisci i due turni
                        if last_combined_end == start:
                            combined_shifts[-1] = (last_combined_start, end, employee_id, [analytic_id,last_combined_analytic[0]])
                            combined_analytics.append(analytic_id)
                        else:
                            # Altrimenti, aggiungi il turno attuale
                            combined_shifts.append((start, end, employee_id, [analytic_id]))
                            combined_analytics.append(analytic_id)
                    else:
                        # Se è il primo turno, aggiungi direttamente
                        combined_shifts.append((start, end, employee_id, [analytic_id]))
                        combined_analytics.append(analytic_id)
    
            # Stampa i risultati
            for i, (start, end, employee_id, analytic_id ) in enumerate(combined_shifts, start=1):
                _logger.info(combined_analytics)
                _logger.info(f"Turno {i}: {start} - {end} - {employee_id.id} - {analytic_id}")
                self.env['account.analytic.line.pwork'].create({
                    'employee_id': employee_id.id,
                    'validated_status': 'validated',
                    'analytic_ids': analytic_id,
                    'datetime_start': start,
                    'datetime_stop': end,
                })

  