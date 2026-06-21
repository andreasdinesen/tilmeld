// Styrer tilmeldings-/redigeringsformularen:
// - "Tilmeld/Gem" er deaktiveret indtil der er tastet et navn.
// - "Deltager ikke"-fluebenet kan ikke sættes før der er tastet et navn.
// - Ved ny tilmelding (autoDecline) sendes formularen automatisk når afbud krydses af.
function initSignupForm(formId, opts) {
  opts = opts || {};
  var form = document.getElementById(formId);
  if (!form) return;
  var name = form.querySelector('input[name=name]');
  var submitBtn = form.querySelector('button[type=submit]');
  var declines = form.querySelectorAll('input[data-decline]');

  function nameOk() { return name && name.value.trim().length > 0; }

  function refresh() {
    if (submitBtn) submitBtn.disabled = !nameOk();
    declines.forEach(function (cb) {
      // lås kun et ikke-afkrydset felt; et allerede afkrydset må gerne kunne fjernes
      if (!cb.checked) cb.disabled = !nameOk();
    });
  }

  if (name) name.addEventListener('input', refresh);

  declines.forEach(function (cb) {
    cb.addEventListener('change', function () {
      if (cb.checked && opts.autoDecline) {
        if (!nameOk()) { cb.checked = false; return; }
        form.submit();
      }
    });
  });

  refresh();
}
