#!/bin/sh
# Запускается образом nginx перед стартом (/docker-entrypoint.d): раз в 6 часов перечитать настройки — так Nginx
# подхватывает сертификат, который продлил сервис certbot (шаг 8.4).
(while :; do sleep 21600; nginx -s reload; done) &
