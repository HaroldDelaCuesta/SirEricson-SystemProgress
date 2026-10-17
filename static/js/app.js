// Back/forward cache: if the browser restores an old copy of a page, reload it so the server can check the login.
window.addEventListener('pageshow', function (e) { if (e.persisted) { window.location.reload(); } });

// Show/hide password
document.querySelectorAll('[data-toggle-pw]').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var input = btn.previousElementSibling;
    var show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.textContent = show ? 'Hide' : 'Show';
  });
});

// Dialogs
document.querySelectorAll('[data-open]').forEach(function (btn) {
  btn.addEventListener('click', function () { document.getElementById(btn.dataset.open).showModal(); });
});
document.querySelectorAll('[data-close]').forEach(function (btn) {
  btn.addEventListener('click', function () { btn.closest('dialog').close(); });
});

// Confirm before risky actions
document.querySelectorAll('[data-confirm]').forEach(function (btn) {
  btn.addEventListener('click', function (e) { if (!confirm(btn.dataset.confirm)) { e.preventDefault(); } });
});

// Light / dark theme (remembered on this device)
document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('portal-theme', next); } catch (e) {}
  });
});

// Notification bell
(function () {
  var btn = document.getElementById('bell');
  if (!btn) { return; }
  var panel = document.getElementById('bell-panel');
  var list = document.getElementById('bell-list');
  var badge = document.getElementById('bell-count');
  var csrf = document.querySelector('meta[name="csrf"]').content;

  function setCount(n) {
    badge.textContent = n > 99 ? '99+' : String(n);
    badge.hidden = !n;
  }
  function load(render) {
    fetch('/notifications/recent', { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) { return; }
        setCount(d.count);
        if (!render) { return; }
        list.textContent = '';
        if (!d.items.length) {
          var empty = document.createElement('li');
          empty.className = 'bell-empty';
          empty.textContent = 'No notifications yet.';
          list.appendChild(empty);
        }
        d.items.forEach(function (i) {
          var li = document.createElement('li');
          var a = document.createElement('a');
          a.href = i.url;
          if (!i.read) { a.className = 'unread'; }
          var msg = document.createElement('span');
          msg.textContent = i.message;
          var when = document.createElement('small');
          when.textContent = i.when;
          a.appendChild(msg); a.appendChild(when); li.appendChild(a); list.appendChild(li);
        });
      });
  }
  function close() { panel.hidden = true; btn.setAttribute('aria-expanded', 'false'); }

  btn.addEventListener('click', function (e) {
    e.stopPropagation();
    var opening = panel.hidden;
    panel.hidden = !opening;
    btn.setAttribute('aria-expanded', opening ? 'true' : 'false');
    if (opening) { load(true); }
  });
  document.addEventListener('click', function (e) { if (!panel.contains(e.target)) { close(); } });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { close(); } });
  document.getElementById('bell-readall').addEventListener('click', function () {
    var fd = new FormData();
    fd.append('csrf_token', csrf);
    fetch('/notifications/read-all', { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
      .then(function () { load(true); });
  });
  setInterval(function () { if (!document.hidden) { load(false); } }, 60000);
})();
