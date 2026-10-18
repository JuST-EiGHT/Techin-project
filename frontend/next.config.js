module.exports = {
  output: 'standalone', // прод-образ: сервер Next без исходников (docker/frontend/Dockerfile.prod)
  poweredByHeader: false, // без «X-Powered-By: Next.js» — не подсказываем, на чём сайт
}
