{
    'name': 'Export hours to Pwork',
    'version': '17.2',
    'author': "Luca Cocozza",
    'application': True,
    'description': "Con questo modulo è possibile esportare le ore dal Tms a Pwork",
    'depends': [
        'base','gtms','hr_timesheet', 'gtms_inspection_survey', 'gtms_fleet_organization','carburante', 'base_automation', 'Diritti', 'fleet', 'hr1'],
    'data': [
        # # # Settaggi per accesso ai contenuti
        'data/ir.model.access.csv',
        # 'data/scheduled_action.xml',
        'data/automation_rule.xml',
        # # # Caricamento delle view,
        'view/trip_states.xml',
        'view/trip.xml',
        'view/trip_type.xml',
        'view/res_config_settings.xml',
        'view/account_analytic_line.xml',
        'view/account_analytic_line_pwork.xml',
        # # Menu
        # 'view/menu.xml',
    ],
}
