(() => {
  "use strict";
  function render(root) {
    root.innerHTML =
      '<section class="am-panel">' +
        '<h2>Aliases</h2>' +
        '<p>Раздел подготовлен. Подключение addy.io API, список FREE / USED aliases, CREATE ALIAS, ASSIGN и REFRESH — следующий этап.</p>' +
        '<div class="am-step"><strong>ADDY_API_TOKEN</strong><small>Токен будет читаться только backend-ом и никогда не попадёт во frontend.</small></div>' +
      '</section>';
  }
  window.PostingTTIIAliases = { render };
})();
