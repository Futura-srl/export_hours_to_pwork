{
    'name': 'Export hours to Pwork',
    'version': '16',
    'author': "Luca Cocozza",
    'application': True,
    'description': "Con questo modulo è possibile esportare le ore dal Tms a Pwork",
    'depends': [
        'gtms','hr_timesheet', 'gtms_inspection_survey'],
    'data': [
        # # # Settaggi per accesso ai contenuti
        # 'data/ir.model.access.csv',
        # # # Caricamento delle view,
        'view/trip_states.xml',
        'view/trip.xml',
        'view/res_config_settings.xml',
        'view/account_analytic_line.xml',
        # # Menu
        # 'view/menu.xml',
    ],
}
