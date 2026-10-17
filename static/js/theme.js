// Runs before the page paints, so there is no flash of the wrong theme.
(function () {
  var theme = 'dark';
  try { theme = localStorage.getItem('portal-theme') === 'light' ? 'light' : 'dark'; } catch (e) {}
  document.documentElement.setAttribute('data-theme', theme);
})();
