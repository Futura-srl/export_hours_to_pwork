from odoo import api, fields, models, http, _, Command
import logging, datetime, requests, json
import xml.etree.ElementTree as ET
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)
now = datetime.datetime.now()


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    datetime_start = fields.Datetime()
    datetime_stop = fields.Datetime()
    gtms_id = fields.Many2one('gtms.trip')
    validated_status = fields.Selection([('draft','Draft'),('validated','Validated'),('processing','Processing'),('done','On Pwork'),('error','Error')])
    error_txt = fields.Text(string='Response')
    processed = fields.Boolean(default=False)
    pwork = fields.Boolean(default=False)
    

    @api.depends('validated', 'processed','error_txt', 'pwork')
    def _compute_validated_status(self):
        for line in self:
            if line.validated and line.processed == False and line.error_txt == False:
                line.validated_status = 'validated'
            elif line.validated and line.processed == True and line.error_txt == False:
                line.validated_status = 'processing'
            elif line.validated and line.processed == True and line.pwork == True and line.error_txt != False:
                line.validated_status = 'done'
            elif line.error_txt != False and line.pwork == False:
                line.validated_status = 'error'
            else:
                line.validated_status = 'draft'

    def processing(self):
        for record in self:
            if record.validated_status == 'validated':
                record.processed = True
            else:
                raise ValidationError(_(f"Il timesheet con id {record.id} deve essere prima sullo stato 'Validato'"))

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
                response, element = self.send_timesheet(badge['name'],data_e,ore_e,minuti_e,secondi_e,data_u,ore_u,minuti_u,secondi_u)
                self.pwork = response
                self.error_txt = element




    def send_timesheet(self,badge,data_e,ore_e,minuti_e,secondi_e,data_u,ore_u,minuti_u,secondi_u):
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
                    </RequestTimbra>
                </Params>
                <ReturnType>FormatJson</ReturnType>
            </setTimbra>
        </soap12:Body>
    </soap12:Envelope>'''.format(pwork_token,pwork_cod_azienda,badge,data_e,ore_e,minuti_e,secondi_e,badge,data_u,ore_u,minuti_u,secondi_u)

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
                return False, data['ckResponse']['MessaggioErrore']
        else:
            if data['ckResponse']['Esito'] == 2:
                return True, data
        

