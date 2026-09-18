-- Neutralizzazione dell'export ore verso PWork: stessi parametri del connettore, ma con il
-- prefisso del modulo. Senza questo, una copia continua a puntare al gestionale vero.
UPDATE ir_config_parameter
   SET value = 'neutralized'
 WHERE key IN ('export_hours_to_pwork.pwork_ip',
               'export_hours_to_pwork.pwork_username',
               'export_hours_to_pwork.pwork_password',
               'export_hours_to_pwork.pwork_session',
               'export_hours_to_pwork.pwork_token');

-- Il codice azienda dice *a quale* anagrafica PWork si sta scrivendo: lasciarlo valido
-- significa che una credenziale reinserita per prova punta subito all'azienda vera.
UPDATE ir_config_parameter
   SET value = 'neutralized'
 WHERE key = 'export_hours_to_pwork.pwork_cod_azienda';
