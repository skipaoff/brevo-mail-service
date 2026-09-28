"""Транзакционная почта через Brevo: коды подтверждения, сброс пароля,
уведомления, журнал писем.

Поднимается отдельным сервисом (mailservice.api) или, если бэкенд на Python,
импортируется напрямую: codes.issue / codes.check, journal.send,
journal.send_template, letters.*.
"""
__version__ = "1.1.0"
