(function () {
  function getShell() {
    return document.querySelector('[data-admin-shell="page"]');
  }

  function sameOrigin(url) {
    try {
      const u = new URL(url, window.location.origin);
      return u.origin === window.location.origin;
    } catch (e) {
      return false;
    }
  }

  function isSamePageAnchor(href) {
    try {
      const url = new URL(href, window.location.origin);
      return url.origin === window.location.origin && url.pathname === window.location.pathname && url.hash;
    } catch (e) {
      return false;
    }
  }

  function scrollToTarget(selector) {
    const el = document.querySelector(selector);
    if (!el) return;
    if (selector && selector.startsWith('#')) {
      history.replaceState({}, '', window.location.pathname + window.location.search + selector);
      updateAdminReturnTargets(selector);
    }
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function swapPageFromHtml(html, url, options = {}) {
    const { push = true, preserveScroll = false, restoreY = 0 } = options;
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
    const newShell = doc.querySelector('[data-admin-shell="page"]');
    const currentShell = getShell();
    if (!newShell || !currentShell) {
      window.location.href = url;
      return;
    }

    document.title = doc.title || document.title;
    currentShell.replaceWith(newShell);
    if (push) history.pushState({}, '', url);

    initAdminPage();

    if (preserveScroll) {
      window.scrollTo({ top: restoreY, behavior: 'auto' });
    } else {
      const hash = new URL(url, window.location.origin).hash;
      if (hash) {
        requestAnimationFrame(() => scrollToTarget(hash));
      } else {
        window.scrollTo({ top: 0, behavior: 'auto' });
      }
    }
  }

  async function visit(url, options = {}) {
    const response = await fetch(url, {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      cache: 'no-store'
    });
    const html = await response.text();
    if (!response.ok || !html) {
      window.location.href = url;
      return;
    }
    await swapPageFromHtml(html, response.url || url, options);
  }

  async function submitForm(form, submitter) {
    const method = String(form.method || 'POST').toUpperCase();
    const actionUrl = new URL(form.action, window.location.origin);
    const preserveScroll = true;
    const restoreY = window.scrollY;
    const fetchOptions = { method, headers: { 'X-Requested-With': 'XMLHttpRequest' } };

    if (method === 'GET') {
      const params = new URLSearchParams(new FormData(form));
      actionUrl.search = params.toString();
    } else {
      const formData = new FormData(form);
      if (submitter && submitter.name && !formData.has(submitter.name)) {
        formData.append(submitter.name, submitter.value || '');
      }
      fetchOptions.body = formData;
    }

    const response = await fetch(actionUrl.toString(), fetchOptions);
    const html = await response.text();
    if (!response.ok || !html) {
      window.location.reload();
      return;
    }
    await swapPageFromHtml(html, response.url || actionUrl.toString(), { push: true, preserveScroll, restoreY });
  }

  function bindAdminQuantityControls() {
    document.querySelectorAll('.btn-qtd-step').forEach((button) => {
      if (button.dataset.stepBound === '1') return;
      button.dataset.stepBound = '1';
      button.addEventListener('click', () => {
        const box = button.closest('[data-quantity-box]');
        const input = box ? box.querySelector('[data-quantity-input]') : null;
        if (!input) return;
        const step = Number(button.dataset.step || 0);
        const min = Number(input.min || 0);
        const max = input.max ? Number(input.max) : Infinity;
        const current = Number(input.value || 0);
        const next = Math.min(max, Math.max(min, current + step));
        input.value = next;
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
    });

    document.querySelectorAll('[data-quantity-input]').forEach((input) => {
      if (input.dataset.quantityBound === '1') return;
      input.dataset.quantityBound = '1';
      input.addEventListener('input', () => {
        const item = input.closest('.item-edicao-pedido');
        if (!item) return;
        item.classList.toggle('item-marcado-remover', Number(input.value || 0) === 0);
      });
    });

    document.querySelectorAll('[data-remove-item]').forEach((button) => {
      if (button.dataset.removeBound === '1') return;
      button.dataset.removeBound = '1';
      button.addEventListener('click', () => {
        const item = button.closest('.item-edicao-pedido');
        if (!item) return;
        const input = item.querySelector('[data-quantity-input]');
        if (!input) return;
        if (!window.confirm('Remover este item do pedido e devolver ao estoque?')) return;
        input.value = 0;
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
    });
  }


  function bindQuickOrderCreator() {
    const lista = document.getElementById('listaItensPedidoRapido');
    const adicionar = document.getElementById('adicionarLinhaPedidoRapido');
    if (!lista || !adicionar || adicionar.dataset.quickOrderBound === '1') return;
    adicionar.dataset.quickOrderBound = '1';

    function bindRemove(button) {
      if (!button || button.dataset.quickRemoveBound === '1') return;
      button.dataset.quickRemoveBound = '1';
      button.addEventListener('click', () => {
        const rows = lista.querySelectorAll('[data-item-row]');
        if (rows.length <= 1) return;
        const row = button.closest('[data-item-row]');
        if (row) row.remove();
      });
    }

    lista.querySelectorAll('[data-remove-quick-row]').forEach(bindRemove);

    adicionar.addEventListener('click', () => {
      const first = lista.querySelector('[data-item-row]');
      if (!first) return;
      const clone = first.cloneNode(true);
      clone.querySelectorAll('select').forEach((el) => { el.value = ''; });
      clone.querySelectorAll('input').forEach((el) => { el.value = el.name === 'item_quantidade[]' ? '1' : ''; });
      lista.appendChild(clone);
      bindRemove(clone.querySelector('[data-remove-quick-row]'));
    });
  }

  function bindAdminMobileNav() {
    document.querySelectorAll('[data-admin-mobile-nav]').forEach((select) => {
      if (select.dataset.mobileNavBound === '1') return;
      select.dataset.mobileNavBound = '1';
      select.addEventListener('change', async () => {
        const value = select.value;
        if (!value) return;
        if (value.startsWith('#')) {
          history.replaceState({}, '', value);
          updateAdminReturnTargets(value);
          scrollToTarget(value);
          return;
        }
        if (value.startsWith('scroll:')) {
          scrollToTarget(value.replace('scroll:', ''));
          select.value = '';
          return;
        }
        try {
          if (sameOrigin(value)) await visit(value, { push: true, preserveScroll: false, restoreY: 0 });
          else window.location.href = value;
        } catch (err) {
          window.location.href = value;
        } finally {
          if (!select.hasAttribute('data-admin-sync-scroll')) select.value = '';
        }
      });
    });
  }


  function updateAdminReturnTargets(hashValue) {
    const suffix = hashValue && hashValue.startsWith('#') ? hashValue : (window.location.hash || '');
    const value = window.location.pathname + window.location.search + suffix;
    document.querySelectorAll('input[name="return_to"]').forEach((input) => {
      input.value = value;
    });
  }

  function bindSaboresSelectState() {
    const select = document.querySelector('#adminSaboresSelect[data-admin-sabores-nav]');
    if (!select) return;

    const sync = () => {
      const current = window.location.hash || '#topoSabores';
      if (select.querySelector('option[value="' + current + '"]')) {
        select.value = current;
      } else {
        select.value = '#topoSabores';
      }
    };

    if (select.dataset.saboresStateBound !== '1') {
      select.dataset.saboresStateBound = '1';
      window.addEventListener('hashchange', sync);
      document.addEventListener('click', (event) => {
        const anchor = event.target.closest('a[href^="#sabor-"]');
        if (!anchor) return;
        const href = anchor.getAttribute('href');
        if (href) {
          sync();
          updateAdminReturnTargets(href);
        }
      });
    }

    requestAnimationFrame(sync);
  }

  function initAdminPage() {
    bindAdminQuantityControls();
    bindQuickOrderCreator();
    bindAdminMobileNav();
    bindSaboresSelectState();
    updateAdminReturnTargets();
  }

  document.addEventListener('click', async (e) => {
    const scrollBtn = e.target.closest('[data-scroll-target]');
    if (scrollBtn) {
      e.preventDefault();
      scrollToTarget(scrollBtn.getAttribute('data-scroll-target'));
      return;
    }

    const ajaxLink = e.target.closest('a.js-admin-ajax-link');
    if (ajaxLink) {
      const href = ajaxLink.getAttribute('href');
      if (!href || ajaxLink.target === '_blank' || !sameOrigin(href)) return;
      e.preventDefault();
      try {
        const preserveScroll = ajaxLink.dataset.preserveScroll === '1';
        await visit(href, { push: true, preserveScroll, restoreY: window.scrollY });
      } catch (err) {
        window.location.href = href;
      }
      return;
    }

    const normalLink = e.target.closest('a[href]');
    if (!normalLink || normalLink.target === '_blank') return;
    const href = normalLink.getAttribute('href') || '';
    if (href.startsWith('javascript:')) return;
    if (isSamePageAnchor(href)) {
      e.preventDefault();
      const hash = new URL(href, window.location.origin).hash;
      scrollToTarget(hash);
    }
  }, true);

  document.addEventListener('submit', async (e) => {
    const form = e.target.closest('form.js-admin-ajax-form');
    if (!form) return;
    e.preventDefault();
    try {
      await submitForm(form, e.submitter || null);
    } catch (err) {
      window.location.reload();
    }
  }, true);

  window.addEventListener('popstate', () => {
    visit(window.location.href, { push: false, preserveScroll: true, restoreY: 0 }).catch(() => window.location.reload());
  });

  initAdminPage();
  window.initAdminPage = initAdminPage;
  window.AdminAjax = { visit, submitForm, scrollToTarget, initAdminPage };
})();
