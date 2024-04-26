from odoo import api, fields, models, http, _, Command
import logging, datetime, requests, json, pytz
import xml.etree.ElementTree as ET
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)
now = datetime.datetime.now()


class AccountAnalyticLine(models.Model):
    _name = "account.analytic.line.pwork"
    _description = "account.analytic.line.pwork"

    name = fields.Char()
    employee_id = fields.Many2one('hr.employee')
    datetime_start = fields.Datetime()
    datetime_stop = fields.Datetime()
    analytic_ids = fields.Many2many('account.analytic.line')
    validated_status = fields.Selection([('draft','Draft'),('validated','Validated'),('done','On Pwork'),('error','Error')], compute='_compute_validated_status')
    error_txt = fields.Text(string='Response')
    pwork = fields.Boolean(default=False)
    unit_amount = fields.Float(string="Hours Spent", compute="_compute_unit_amount")
    causale_gtms_pwork = fields.Char(string="Causale gtms Pwork", compute="_compute_causale_gtms_pwork")


    
    @api.depends('analytic_ids')
    def _compute_causale_gtms_pwork(self):
        for record in self:
            for r in record.analytic_ids: 
                record.causale_gtms_pwork = str(r.gtms_id.trip_type_id.causale_pwork)
        

    
    @api.onchange('datetime_start', 'datetime_stop')
    def _compute_unit_amount(self):
        _logger.info(line.unit_amount)
        for line in self:        
            if line.datetime_start != False and line.datetime_stop != False:
                work_time = line.datetime_stop - line.datetime_start
                working_seconds = work_time.total_seconds() / 3600.0
                line.unit_amount = working_seconds
                _logger.info(line.unit_amount)


    
    @api.depends('error_txt', 'pwork')
    def _compute_validated_status(self):
        for line in self:
            if line.pwork == True and line.error_txt != False:
                line.validated_status = 'done'
            elif line.error_txt != False and line.pwork == False:
                line.validated_status = 'error'
            else:
                line.validated_status = 'validated'

    def upload_to_pwork(self):
        tz = pytz.timezone('Europe/Rome')  # E.g., 'Europe/Rome'
        for record in self:
            # if record.validated_status == 'processing' or record.validated_status == 'error':
            data_e = record.datetime_start.astimezone(tz).strftime("%d/%m/%Y")
            ore_e = record.datetime_start.astimezone(tz).strftime("%H")
            minuti_e = record.datetime_start.astimezone(tz).strftime("%M")
            secondi_e = record.datetime_start.astimezone(tz).strftime("%S")
            data_u = record.datetime_stop.astimezone(tz).strftime("%d/%m/%Y")
            ore_u = record.datetime_stop.astimezone(tz).strftime("%H")
            minuti_u = record.datetime_stop.astimezone(tz).strftime("%M")
            secondi_u = record.datetime_stop.astimezone(tz).strftime("%S")
            causale_pwork = record.causale_gtms_pwork
            
            # Recupero il badge del dipendente
            badges = self.env['hr.badgespwork'].search_read([('active', '=', True), ('hr_id', '=', record.employee_id.id)],limit=1)
            for badge in badges:
                _logger.info("Badge")
            _logger.info(f"Stampo badge {badge['name']}")
            _logger.info(f"Stampo data_e {data_e}")
            _logger.info(f"Stampo ore_e {ore_e}")
            _logger.info(f"Stampo minuti_e {minuti_e}")
            _logger.info(f"Stampo secondi_e {secondi_e}")
            _logger.info(f"Stampo data_u {data_u}")
            _logger.info(f"Stampo ore_u {ore_u}")
            _logger.info(f"Stampo minuti_u {minuti_u}")
            _logger.info(f"Stampo secondi_u {secondi_u}")
            response, element, error = self.env['account.analytic.line.pwork'].send_timesheet(badge['name'],data_e,ore_e,minuti_e,secondi_e,causale_pwork,data_u,ore_u,minuti_u,secondi_u)
            record.pwork = response
            record.error_txt = element
            for timesheet in record.analytic_ids:
                _logger.info(timesheet.id)
                timesheet_record = self.env['account.analytic.line'].browse(timesheet.id)
                timesheet_record.write({'pwork': response, 'error_txt': element, 'error': error})


    def send_timesheet(self,badge,data_e,ore_e,minuti_e,secondi_e,causale_pwork,data_u,ore_u,minuti_u,secondi_u):
        config_obj = self.env['ir.config_parameter']
        pwork_username = config_obj.sudo().get_param('pwork_username')
        pwork_password = config_obj.sudo().get_param('pwork_password')
        pwork_ip = config_obj.sudo().get_param('pwork_ip')
        pwork_session = config_obj.sudo().get_param('pwork_session')
        pwork_cod_azienda = config_obj.sudo().get_param('pwork_cod_azienda')
        pwork_token = config_obj.sudo().get_param('pwork_token')
        _logger.info(badge)

        _logger.info("Avvio connessione")
        
        url = 'https://futura.presenze-online.it/webservice/ws.asmx'
        headers = {
            'Content-Type': 'application/soap+xml; charset=utf-8',
        }
        
        
        # costruzione del payload della richiesta SOAP XML
        payload = '''<?xml version="1.0" encoding="utf-8"?>
    <soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
        <soap12:Body>
            <setTimbra xmlns="https://presenze-online.it/">
                <Token>{}</Token>
                <CodAzienda>{}</CodAzienda>
                <Params>
                    <RequestTimbra>
                        <Badge>{}</Badge>
                        <Verso>E</Verso>
                        <Data>{}</Data>
                        <HH>{}</HH>
                        <MM>{}</MM>
                        <SS>{}</SS>
                        <Indirizzo>003</Indirizzo>
                        <DataAuto>0</DataAuto>
                        <OraAuto>0</OraAuto>
                        <PCode>0200</PCode>
                        <Causale>{}</Causale>
                    </RequestTimbra>
                    <RequestTimbra>
                        <Badge>{}</Badge>
                        <Verso>U</Verso>
                        <Data>{}</Data>
                        <HH>{}</HH>
                        <MM>{}</MM>
                        <SS>{}</SS>
                        <Indirizzo>003</Indirizzo>
                        <DataAuto>0</DataAuto>
                        <OraAuto>0</OraAuto>
                        <PCode>0200</PCode>
                        <Causale>{}</Causale>
                    </RequestTimbra>
                </Params>
                <ReturnType>FormatJson</ReturnType>
            </setTimbra>
        </soap12:Body>
    </soap12:Envelope>'''.format(pwork_token,pwork_cod_azienda,badge,data_e,ore_e,minuti_e,secondi_e,causale_pwork,badge,data_u,ore_u,minuti_u,secondi_u,causale_pwork)

        _logger.info(payload)
        _logger.info("Invio della richiesta HTTP POST")
    
        # Invio della richiesta HTTP POST
        response = requests.post(url, headers=headers, data=payload)
    
    
        _logger.info("Stampa dello stato della risposta HTTP e del contenuto della risposta")
    
        # stampa dello stato della risposta HTTP e del contenuto della risposta
        _logger.info(response)
        _logger.info(response.status_code)
        _logger.info(response.content)
    
    
        # Analisi del documento XML
        xml_string = response.content
        root = ET.fromstring(xml_string)
    
        # Recupero del valore della stringa JSON
        result = root.find('.//{https://presenze-online.it/}setTimbraResult').text.strip()
        data = json.loads(result)
        _logger.info(data)
        if data['ckResponse']['Esito'] == 1:
            if data['ckResponse']['MessaggioErrore']:
                return False, data['ckResponse']['MessaggioErrore'], True
        else:
            if data['ckResponse']['Esito'] == 2:
                return True, data, False
        

