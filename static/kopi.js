/* Kopiér-knapper: <button data-kopi="feltets-id"> + <p data-kopi-note="feltets-id">.
   Teksten ved held kan sættes med data-kopi-ok på knappen.

   Clipboard-API'et findes KUN i et sikkert kontekst (https eller localhost) — på
   panelets IP:port gør det ikke. Derfor skal feltet være synligt i forvejen, og
   knappen falder tilbage til at markere teksten, så den kan kopieres i hånden.
   En knap, der bare ikke gør noget, er det værste svar.

   Filen deles af admin-siden (delingsteksten) og bruger-forsiden
   (kalender-adressen), så de to ikke kan komme til at opføre sig forskelligt. */
document.querySelectorAll('[data-kopi]').forEach(function (knap) {
  var felt = document.getElementById(knap.dataset.kopi);
  var note = document.querySelector('[data-kopi-note="' + knap.dataset.kopi + '"]');
  if (!felt) return;
  knap.addEventListener('click', function () {
    function sig(t, daarlig) {
      if (!note) return;
      note.textContent = t;
      note.classList.toggle('bad', !!daarlig);
    }
    var ok = knap.dataset.kopiOk || 'Kopieret.';
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(felt.value)
        .then(function () { sig(ok); })
        .catch(function () { felt.select(); sig('Kunne ikke kopiere — teksten er markeret, tryk ⌘C.', true); });
    } else {
      felt.select();
      sig('Teksten er markeret — tryk ⌘C for at kopiere.');
    }
  });
});
