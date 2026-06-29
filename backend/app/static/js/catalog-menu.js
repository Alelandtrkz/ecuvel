(function () {
  'use strict';

  /* ── Catalog dropdown (two-panel hover menu) ── */
  var catalogMenu = document.getElementById('catalog-menu');
  if (catalogMenu) {
    var mainCats  = catalogMenu.querySelectorAll('.catalog-dropdown__main-cat');
    var subPanels = catalogMenu.querySelectorAll('.catalog-dropdown__sub-panel');

    function showPanel(index) {
      mainCats.forEach(function (btn) { btn.classList.remove('is-active'); });
      subPanels.forEach(function (panel) { panel.classList.remove('is-visible'); });
      var activeBtn   = catalogMenu.querySelector('[data-cat-index="' + index + '"]');
      var activePanel = catalogMenu.querySelector('[data-panel-index="' + index + '"]');
      if (activeBtn)   activeBtn.classList.add('is-active');
      if (activePanel) activePanel.classList.add('is-visible');
    }

    mainCats.forEach(function (btn) {
      btn.addEventListener('mouseenter', function () { showPanel(btn.dataset.catIndex); });
    });

    document.addEventListener('click', function (e) {
      if (catalogMenu.open && !catalogMenu.contains(e.target)) {
        catalogMenu.removeAttribute('open');
      }
    });
  }

  /* ── Category modal (search bar filter) ── */
  var modal     = document.getElementById('category-modal');
  if (!modal) return;

  var valueInput = document.getElementById('search-category-value');
  var labelSpan  = document.getElementById('search-category-label');
  var closeBtn   = document.getElementById('close-category-modal');
  var searchBtn  = document.getElementById('search-category-btn');
  var items      = modal.querySelectorAll('.category-modal__item');

  function openModal() {
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    var current = valueInput ? valueInput.value : '';
    items.forEach(function (item) {
      item.classList.toggle('is-selected', item.dataset.slug === current);
    });
    if (closeBtn) closeBtn.focus();
  }

  function closeModal() {
    modal.hidden = true;
    document.body.style.overflow = '';
  }

  if (searchBtn) searchBtn.addEventListener('click', openModal);
  if (closeBtn)  closeBtn.addEventListener('click',  closeModal);

  items.forEach(function (item) {
    item.addEventListener('click', function () {
      if (valueInput) valueInput.value = item.dataset.slug;
      if (labelSpan)  labelSpan.textContent = item.dataset.name;
      closeModal();
    });
  });

  modal.addEventListener('click', function (e) {
    if (e.target === modal) closeModal();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !modal.hidden) closeModal();
  });
})();
