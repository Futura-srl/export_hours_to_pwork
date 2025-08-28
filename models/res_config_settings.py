from odoo import api, fields, models, http, _, Command
import logging, datetime, requests, json
import xml.etree.ElementTree as ET
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'
    
    pwork_username = fields.Char(config_parameter="export_hours_to_pwork.pwork_username")
    pwork_password = fields.Char(config_parameter="export_hours_to_pwork.pwork_password")
    pwork_ip = fields.Char(config_parameter="export_hours_to_pwork.pwork_ip")
    pwork_session = fields.Char(config_parameter="export_hours_to_pwork.pwork_session")
    pwork_cod_azienda = fields.Char(config_parameter="export_hours_to_pwork.pwork_cod_azienda")
    pwork_token = fields.Char(config_parameter="export_hours_to_pwork.pwork_token")

    pwork_test = fields.Boolean(string="Test Mode", config_parameter="export_hours_to_pwork.pwork_test")

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        config = self.env['ir.config_parameter'].sudo()
        res.update(
            pwork_username=config.get_param('export_hours_to_pwork.pwork_username') or '',
            pwork_password=config.get_param('export_hours_to_pwork.pwork_password') or '',
            pwork_ip=config.get_param('export_hours_to_pwork.pwork_ip') or '',
            pwork_session=config.get_param('export_hours_to_pwork.pwork_session') or '',
            pwork_cod_azienda=config.get_param('export_hours_to_pwork.pwork_cod_azienda') or '',
            pwork_token=config.get_param('export_hours_to_pwork.pwork_token') or '',
            pwork_test=config.get_param('export_hours_to_pwork.pwork_test') == 'True',
        )
        return res

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        pwork_username = self.pwork_username
        pwork_password = self.pwork_password
        pwork_ip = self.pwork_ip
        pwork_session = self.pwork_session
        pwork_cod_azienda = self.pwork_cod_azienda
        pwork_token = self.pwork_token
        pwork_test = self.pwork_test

        params = self.env['ir.config_parameter'].sudo()
        params.set_param('export_hours_to_pwork.pwork_username', pwork_username)
        params.set_param('export_hours_to_pwork.pwork_password', pwork_password)
        params.set_param('export_hours_to_pwork.pwork_ip', pwork_ip)
        params.set_param('export_hours_to_pwork.pwork_session', pwork_session)
        params.set_param('export_hours_to_pwork.pwork_cod_azienda', pwork_cod_azienda)
        params.set_param('export_hours_to_pwork.pwork_token', pwork_token)
        params.set_param('export_hours_to_pwork.pwork_test', pwork_test)
        params.set_param('pwork_username', pwork_username)
        params.set_param('pwork_password', pwork_password)
        params.set_param('pwork_ip', pwork_ip)
        params.set_param('pwork_session', pwork_session)
        params.set_param('pwork_cod_azienda', pwork_cod_azienda)
        params.set_param('pwork_token', pwork_token)

    def get_token_from_pwork(self):
        config = self.env['ir.config_parameter'].sudo()

        pwork_username = config.get_param('export_hours_to_pwork.pwork_username') or ''
        pwork_password = config.get_param('export_hours_to_pwork.pwork_password') or ''
        pwork_ip = config.get_param('export_hours_to_pwork.pwork_ip') or ''
        pwork_session = config.get_param('export_hours_to_pwork.pwork_session') or ''
        pwork_cod_azienda = config.get_param('export_hours_to_pwork.pwork_cod_azienda') or ''
        pwork_token = config.get_param('export_hours_to_pwork.pwork_token') or ''


        _logger.info("Avvio connessione")
    
        url = 'https://futura.presenze-online.it/webservice/ws.asmx'
        headers = {
            'Content-Type': 'application/soap+xml; charset=utf-8',
        }
    
        # costruzione del payload della richiesta SOAP XML
        payload = '''<?xml version="1.0" encoding="utf-8"?>
        <soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
            <soap12:Body>
                <getToken xmlns="https://presenze-online.it/">
                    <Params>
                        <Username>{}</Username>
                        <Password>{}</Password>
                        <Ip>{}</Ip>
                        <Session>{}</Session>
                        <CodAzienda>{}</CodAzienda>
                        <NumHHScadToken>4</NumHHScadToken>
                    </Params>
                    <ReturnType>FormatJson</ReturnType>
                </getToken>
            </soap12:Body>
        </soap12:Envelope>'''.format(pwork_username, pwork_password, pwork_ip, pwork_session, pwork_cod_azienda)
    
        _logger.info("Invio della richiesta HTTP POST")
    
        # Invio della richiesta HTTP POST
        response = requests.post(url, headers=headers, data=payload)
    
    
        _logger.info("Stampa dello stato della risposta HTTP e del contenuto della risposta")
    
        # stampa dello stato della risposta HTTP e del contenuto della risposta
        _logger.info(response.status_code)
        _logger.info(response.content)
    
    
        # Analisi del documento XML
        xml_string = response.content
        root = ET.fromstring(xml_string)
    
        # Recupero del valore della stringa JSON
        result = root.find('.//{https://presenze-online.it/}getTokenResult').text.strip()
    
        # Analisi della stringa JSON
        data = json.loads(result)
    
        # Recupero dei valori desiderati dal dizionario
        TOKEN = data['Generics']['UID']
        azienda = data['Generics']['Azienda']
        email = data['Generics']['Email']
        livello = data['Generics']['Livello']
        nome = data['Generics']['Nome']
        data_scadenza = data['Generics']['DataScad']
        data_scadenza_privacy = data['Generics']['DataScadPrivacy']
        data_ultimo_upd = data['Generics']['DataUltimoUpd']
        gruppo_user = data['Generics']['GruppoUser']
        key_public = data['Generics']['KeyPublic']
    
        _logger.info("TOKEN: " + TOKEN)
        _logger.info("azienda: " + azienda)
        _logger.info("email: " + email)
        _logger.info("livello: " + str(livello))
        _logger.info("nome: " + nome)
        _logger.info("data scadenza: " + data_scadenza)
        _logger.info("data scadenza privacy: " + data_scadenza_privacy)
        _logger.info("data ultimo upd: " + data_ultimo_upd)
        _logger.info("gruppo user: " + gruppo_user)
        _logger.info("Key public: " + key_public)

        params = self.env['ir.config_parameter'].sudo()
        params.set_param('export_hours_to_pwork.pwork_username', pwork_username)
        params.set_param('export_hours_to_pwork.pwork_password', pwork_password)
        params.set_param('export_hours_to_pwork.pwork_ip', pwork_ip)
        params.set_param('export_hours_to_pwork.pwork_session', pwork_session)
        params.set_param('export_hours_to_pwork.pwork_cod_azienda', pwork_cod_azienda)
        params.set_param('export_hours_to_pwork.pwork_token', TOKEN)

        config = self.env['ir.config_parameter'].sudo()

        _logger.info({
            'pwork_username': config.get_param('export_hours_to_pwork.pwork_username'),
            'pwork_password': config.get_param('export_hours_to_pwork.pwork_password'),
            'pwork_ip': config.get_param('export_hours_to_pwork.pwork_ip'),
            'pwork_session': config.get_param('export_hours_to_pwork.pwork_session'),
            'pwork_cod_azienda': config.get_param('export_hours_to_pwork.pwork_cod_azienda'),
            'pwork_token': config.get_param('export_hours_to_pwork.pwork_token'),
        })
