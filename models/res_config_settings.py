from odoo import api, fields, models, http, _, Command
import logging, datetime, requests, json
from dateutil.relativedelta import relativedelta
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
    switch_hr1 = fields.Boolean(string="Switch to HR1", config_parameter="export_hours_to_pwork.switch_hr1")

    # Caricamento automatico delle ore e chiusura del mese (vedi pwork.caricamento)
    pwork_metodo_precedente = fields.Boolean(string="Usa il metodo precedente", config_parameter="export_hours_to_pwork.metodo_precedente")
    pwork_caricamento_automatico = fields.Boolean(string="Caricamento automatico ore", config_parameter="export_hours_to_pwork.caricamento_automatico")
    pwork_chiusura_automatica = fields.Boolean(string="Chiusura mese automatica", config_parameter="export_hours_to_pwork.chiusura_automatica")
    pwork_mese_chiuso = fields.Selection([
        ('1', 'Gennaio'), ('2', 'Febbraio'), ('3', 'Marzo'), ('4', 'Aprile'), ('5', 'Maggio'), ('6', 'Giugno'),
        ('7', 'Luglio'), ('8', 'Agosto'), ('9', 'Settembre'), ('10', 'Ottobre'), ('11', 'Novembre'), ('12', 'Dicembre'),
    ], string="Ultimo mese chiuso", config_parameter="export_hours_to_pwork.mese_chiuso")
    pwork_anno_chiuso = fields.Integer(string="Anno dell'ultimo mese chiuso", config_parameter="export_hours_to_pwork.anno_chiuso")
    pwork_giorno_scadenza_chiusura = fields.Integer(string="Giorno di scadenza chiusura", config_parameter="export_hours_to_pwork.giorno_scadenza_chiusura")
    pwork_email_avvisi = fields.Char(string="Email avvisi caricamento", config_parameter="export_hours_to_pwork.email_avvisi")
    pwork_email_hr = fields.Char(string="Email controllo HR", config_parameter="export_hours_to_pwork.email_hr")
    pwork_promemoria_rop = fields.Boolean(string="Promemoria viaggi da chiudere ai ROP", config_parameter="export_hours_to_pwork.promemoria_rop")
    pwork_email_mittente = fields.Char(string="Email mittente promemoria", config_parameter="export_hours_to_pwork.email_mittente")
    # date_controllo_viaggi = fields.Datetime(string="Date controllo viaggi per pagamenti", config_parameter="export_hours_to_pwork.date_controllo_viaggi")
    # pwork_date_controllo_viaggi = fields.Datetime(string="Date controllo viaggi per pagamenti", config_parameter="export_hours_to_pwork.date_controllo_viaggi")

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
            # pwork_date_controllo_viaggi=config.get_param('export_hours_to_pwork.date_controllo_viaggi') or '',

        )
        return res

    def set_values(self):
        # Il mese chiuso si indica con mese e anno insieme, e deve essere un mese gia' finito
        if bool(self.pwork_mese_chiuso) != bool(self.pwork_anno_chiuso):
            raise UserError(_("Per l'ultimo mese chiuso servono sia il mese sia l'anno."))
        if self.pwork_mese_chiuso:
            fine_mese = datetime.date(self.pwork_anno_chiuso, int(self.pwork_mese_chiuso), 1) + relativedelta(months=1)
            if fine_mese > self.env['pwork.caricamento']._oggi():
                raise UserError(_("Non si può chiudere un mese ancora in corso."))
        if self.pwork_giorno_scadenza_chiusura and not 1 <= self.pwork_giorno_scadenza_chiusura <= 31:
            raise UserError(_("Il giorno di scadenza della chiusura deve essere tra 1 e 31 (0 = nessuna scadenza)."))
        super(ResConfigSettings, self).set_values()
        pwork_username = self.pwork_username
        pwork_password = self.pwork_password
        pwork_ip = self.pwork_ip
        pwork_session = self.pwork_session
        pwork_cod_azienda = self.pwork_cod_azienda
        pwork_token = self.pwork_token
        pwork_test = self.pwork_test
        # date_controllo_viaggi = self.date_controllo_viaggi

        params = self.env['ir.config_parameter'].sudo()
        params.set_param('export_hours_to_pwork.pwork_username', pwork_username)
        params.set_param('export_hours_to_pwork.pwork_password', pwork_password)
        params.set_param('export_hours_to_pwork.pwork_ip', pwork_ip)
        params.set_param('export_hours_to_pwork.pwork_session', pwork_session)
        params.set_param('export_hours_to_pwork.pwork_cod_azienda', pwork_cod_azienda)
        params.set_param('export_hours_to_pwork.pwork_token', pwork_token)
        params.set_param('export_hours_to_pwork.pwork_test', pwork_test)
        # params.set_param('export_hours_to_pwork.date_controllo_viaggi', pwork_test)
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
        params.set_param('pwork_username', pwork_username)
        params.set_param('pwork_password', pwork_password)
        params.set_param('pwork_ip', pwork_ip)
        params.set_param('pwork_session', pwork_session)
        params.set_param('pwork_cod_azienda', pwork_cod_azienda)
        params.set_param('pwork_token', TOKEN)

        config = self.env['ir.config_parameter'].sudo()

        _logger.info({
            'pwork_username': config.get_param('export_hours_to_pwork.pwork_username'),
            'pwork_password': config.get_param('export_hours_to_pwork.pwork_password'),
            'pwork_ip': config.get_param('export_hours_to_pwork.pwork_ip'),
            'pwork_session': config.get_param('export_hours_to_pwork.pwork_session'),
            'pwork_cod_azienda': config.get_param('export_hours_to_pwork.pwork_cod_azienda'),
            'pwork_token': config.get_param('export_hours_to_pwork.pwork_token'),
        })
