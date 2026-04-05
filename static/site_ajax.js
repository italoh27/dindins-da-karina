(function () {
  function normalizeFlavorKey(text) {
    return String(text || '').trim().toLowerCase().replace(/\s+/g, '-');
  }

  function findCartRow(payload) {
    if (!payload) return null;
    if (payload.flavor_key) {
      const byKey = document.querySelector('[data-flavor-key= + payload.flavor_key + ]');
      if (byKey) return byKey;
    }
    if (payload.removed_name) {
      const byRemovedName = document.querySelector('[data-flavor-key= + normalizeFlavorKey(payload.removed_name) + ]');
      if (byRemovedName) return byRemovedName;
    }
    if (payload.item_name) {
      const byItemName = document.querySelector('[data-flavor-key= + normalizeFlavorKey(payload.item_name) + ]');
      if (byItemName) return byItemName;
    }
    if (typeof payload.removed_index !== 'undefined') return document.querySelector('[data-cart-item= + payload.removed_index + ]');
    if (typeof payload.item_index !== 'undefined') return document.querySelector('[data-cart-item= + payload.item_index + ]');
    return null;
  }

  function syncTopCartCounter() {
    const cartLink = document.querySelector('.btn-carrinho-topo');
    if (!cartLink) return;

    const explicit = document.body && document.body.dataset ? document.body.dataset.cartCount : '';
    if (explicit !== '') {
      const total = Math.max(0, Number(explicit || 0));
      cartLink.textContent = '🛒 Carrinho (' + total + ')';
      cartLink.dataset.cartCountLabel = String(total);
      return;
    }

    const quantityInputs = document.querySelectorAll('.input-quantidade-carrinho');
    if (!quantityInputs.length) return;

    const total = Array.from(quantityInputs).reduce((sum, input) => {
      return sum + Math.max(0, Number(input.value || 0));
    }, 0);
    cartLink.textContent = '🛒 Carrinho (' + total + ')';
    cartLink.dataset.cartCountLabel = String(total);
  }

  function getShell() {
    return document.querySelector('[data-ajax-shell="public"]');
  }

  function showToast(text) {
    const toast = document.getElementById('toastGlobal');
    if (!toast) return;
    toast.textContent = text;
    toast.hidden = false;
    toast.classList.add('mostrar');
    setTimeout(() => {
      toast.classList.remove('mostrar');
      setTimeout(() => { toast.hidden = true; }, 250);
    }, 2200);
  }

  function sameOrigin(url) {
    try {
      const u = new URL(url, window.location.origin);
      return u.origin === window.location.origin;
    } catch (e) {
      return false;
    }
  }

  function setButtonLoading(button, loading) {
    if (!button || button.classList.contains('btn-remover-item-x')) return;

    const isFastAction = button.classList.contains('btn-adicionar') || button.classList.contains('btn-adicionar-pro');

    if (loading) {
      if (!button.dataset.originalText) button.dataset.originalText = button.innerHTML;
      if (isFastAction) {
        button.classList.add('is-loading-soft');
        return;
      }
      button.disabled = true;
      button.classList.add('is-loading');
      button.innerHTML = button.dataset.loadingText || 'Carregando...';
    } else {
      if (!isFastAction) {
        button.disabled = false;
        if (button.dataset.originalText) button.innerHTML = button.dataset.originalText;
      }
      button.classList.remove('is-loading');
      button.classList.remove('is-loading-soft');
    }
  }

  async function swapPageFromHtml(html, url, push, restoreScrollY) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
    const newShell = doc.querySelector('[data-ajax-shell="public"]');
    const currentShell = getShell();
    if (!newShell || !currentShell) {
      window.location.href = url;
      return;
    }
    document.title = doc.title || document.title;
    currentShell.replaceWith(newShell);
    if (push) history.pushState({}, '', url);
    bind();
    syncTopCartCounter();
    if (typeof restoreScrollY === 'number') {
      window.scrollTo({ top: restoreScrollY, behavior: 'auto' });
    }
  }

  async function visit(url, push=true) {
    const preserve = window.scrollY;
    const response = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, cache: 'no-store' });
    const html = await response.text();
    if (!response.ok) {
      window.location.href = url;
      return;
    }
    await swapPageFromHtml(html, url, push, preserve);
  }

  async function copyPix(value, inputEl) {
    const valor = (value || '').trim();
    if (!valor) throw new Error('empty');
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(valor);
      return true;
    }
    const temp = inputEl || document.createElement('textarea');
    if (!inputEl) {
      temp.value = valor;
      temp.setAttribute('readonly', 'readonly');
      temp.style.position = 'fixed';
      temp.style.left = '-9999px';
      document.body.appendChild(temp);
    }
    try {
      temp.removeAttribute('readonly');
      temp.focus();
      temp.select();
      if (temp.setSelectionRange) temp.setSelectionRange(0, 99999);
      const ok = document.execCommand('copy');
      if (!ok) throw new Error('copy_failed');
      return true;
    } finally {
      if (!inputEl && temp.parentNode) temp.parentNode.removeChild(temp);
      else if (inputEl) inputEl.setAttribute('readonly', 'readonly');
    }
  }


  function formatMoney(value) {
    const number = Number(value || 0);
    return 'R$ ' + number.toFixed(2);
  }

  function updateCartCounter(total) {
    const cartLink = document.querySelector('.btn-carrinho-topo');
    if (!cartLink) return;
    const safe = Math.max(0, Number(total || 0));
    cartLink.textContent = '🛒 Carrinho (' + safe + ')';
    cartLink.dataset.cartCountLabel = String(safe);
    if (document.body && document.body.dataset) {
      document.body.dataset.cartCount = String(safe);
    }
  }

  function reindexCartDom() {
    document.querySelectorAll('[data-cart-item]').forEach((row, index) => {
      row.dataset.cartItem = String(index);
      const removeForm = row.querySelector('[data-cart-remove-form]');
      if (removeForm) {
        removeForm.dataset.cartRemoveForm = String(index);
        removeForm.action = '/remover_item/' + index;
      }
      const qtyForm = row.querySelector('.form-quantidade-carrinho');
      if (qtyForm) qtyForm.action = '/carrinho/atualizar/' + index;
      const qtyInput = row.querySelector('[data-cart-qty-input]');
      if (qtyInput) qtyInput.setAttribute('data-cart-qty-input', String(index));
      const subtotal = row.querySelector('[data-cart-subtotal]');
      if (subtotal) subtotal.setAttribute('data-cart-subtotal', String(index));
    });
  }

  function updateFlavorStock(flavorKey, stock) {
    if (!flavorKey) return;
    const badge = document.querySelector('[data-estoque-sabor="' + flavorKey + '"]');
    const input = document.querySelector('[data-flavor-qty-input="' + flavorKey + '"]');
    const button = document.querySelector('[data-flavor-button="' + flavorKey + '"]');
    const safeStock = Math.max(0, Number(stock || 0));
    if (badge) {
      if (safeStock > 0) {
        badge.textContent = safeStock + ' unidade(s)';
        badge.classList.remove('badge-esgotado');
        badge.classList.add('badge-estoque');
        badge.setAttribute('data-estoque-sabor', flavorKey);
      } else {
        badge.textContent = 'Sem estoque';
        badge.classList.remove('badge-estoque');
        badge.classList.add('badge-esgotado');
      }
    }
    if (input) {
      input.max = String(safeStock);
      if (safeStock <= 0) {
        input.value = '1';
        input.disabled = true;
      } else {
        input.disabled = false;
        const current = Math.max(1, Number(input.value || 1));
        input.value = String(Math.min(current, safeStock));
      }
    }
    if (button) {
      if (safeStock <= 0) {
        button.disabled = true;
        button.textContent = 'Indisponível';
      } else {
        button.disabled = false;
        if (!button.classList.contains('is-loading-soft')) {
          button.textContent = 'Adicionar ao carrinho';
        }
      }
    }
  }

  function updateCartItemDom(payload) {
    if (!payload) return;
    updateCartCounter(payload.cart_count);
    const totalEl = document.querySelector('[data-cart-total]');
    if (totalEl && payload.total_text) totalEl.textContent = payload.total_text;

    if (payload.removed) {
      const rowToRemove = findCartRow(payload);
      if (rowToRemove) rowToRemove.remove();
      reindexCartDom();
      if (!document.querySelector('[data-cart-item]')) {
        visit('/carrinho', false).catch(() => window.location.reload());
      }
      return;
    }

    if (typeof payload.item_index !== 'undefined' || payload.item_name || payload.flavor_key) {
      const row = findCartRow(payload);
      if (!row) {
        visit('/carrinho', false).catch(() => window.location.reload());
        return;
      }
      const rowIndex = row.dataset.cartItem || payload.item_index;
      const input = row.querySelector('[data-cart-qty-input="' + rowIndex + '"]') || row.querySelector('[data-cart-qty-input]');
      const subtotal = row.querySelector('[data-cart-subtotal="' + rowIndex + '"]') || row.querySelector('[data-cart-subtotal]');
      if (input) {
        input.value = String(payload.item_quantity || input.value || 1);
        if (typeof payload.estoque_maximo !== 'undefined') {
          input.max = String(payload.estoque_maximo);
        }
      }
      if (subtotal && payload.item_subtotal_text) subtotal.textContent = payload.item_subtotal_text;
      if (payload.flavor_key && typeof payload.estoque_exibicao !== 'undefined') {
        updateFlavorStock(payload.flavor_key, payload.estoque_exibicao);
      }
    }
  }

  function handleJsonFormSuccess(form, payload, button) {
    const action = form.getAttribute('action') || '';
    if (action === '/pedido') {
      updateCartCounter(payload.cart_count);
      if (payload.flavor_key) updateFlavorStock(payload.flavor_key, payload.estoque_exibicao);
      if (button) {
        button.classList.add('is-success-flash');
        const originalText = button.dataset.originalText || 'Adicionar ao carrinho';
        button.textContent = '✔ Adicionado';
        window.setTimeout(() => {
          button.classList.remove('is-success-flash');
          if (!button.disabled) button.textContent = originalText;
        }, 900);
      }
      showToast(payload.message || 'Adicionado com sucesso.');
      return;
    }

    if (action === '/limpar_carrinho') {
      updateCartCounter(0);
      showToast(payload.message || 'Carrinho limpo com sucesso.');
      visit('/carrinho', false).catch(() => window.location.reload());
      return;
    }

    if (action.indexOf('/remover_item/') === 0 || action.indexOf('/carrinho/atualizar/') === 0) {
      updateCartItemDom(payload);
      showToast(payload.message || 'Atualizado com sucesso.');
      return;
    }

    showToast(payload.message || 'Atualizado com sucesso.');
  }

  function bindLinks() {
    document.querySelectorAll('a.js-ajax-link').forEach((link) => {
      if (link.dataset.ajaxBound === '1') return;
      link.dataset.ajaxBound = '1';
      link.addEventListener('click', async (e) => {
        const href = link.getAttribute('href');
        if (!href || link.target === '_blank' || href.startsWith('#') || !sameOrigin(href)) return;
        e.preventDefault();
        try {
          await visit(href, true);
        } catch (err) {
          window.location.href = href;
        }
      });
    });
  }

  function bindForms() {
    document.querySelectorAll('form.js-ajax-form').forEach((form) => {
      if (form.dataset.ajaxBound === '1') return;
      form.dataset.ajaxBound = '1';
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const button = e.submitter || form.querySelector('button[type="submit"]');
        const previousScroll = window.scrollY;
        setButtonLoading(button, true);
        try {
          form.dataset.submitting = '1';
          const formData = new FormData(form);
          const submitter = e.submitter;
          if (submitter && submitter.name && !formData.has(submitter.name)) {
            formData.append(submitter.name, submitter.value || '');
          }
          const response = await fetch(form.action, {
            method: form.method || 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            body: formData,
            cache: 'no-store'
          });
          const contentType = response.headers.get('content-type') || '';
          if (contentType.includes('application/json')) {
            const payload = await response.json();
            if (!response.ok || payload.ok === false) {
              if (payload && payload.estoque_maximo && form.querySelector('[data-quantity-input]')) {
                form.querySelector('[data-quantity-input]').max = String(payload.estoque_maximo);
              }
              const action = form.getAttribute('action') || '';
              if ((action.indexOf('/remover_item/') === 0 || action.indexOf('/carrinho/atualizar/') === 0) && response.status === 404) {
                visit('/carrinho', false).catch(() => window.location.reload());
                return;
              }
              showToast((payload && payload.message) || 'Não foi possível concluir a ação.');
              return;
            }
            handleJsonFormSuccess(form, payload, button);
            syncTopCartCounter();
            return;
          }
          const finalUrl = response.url || form.action;
          const html = await response.text();
          if (response.redirected) {
            await visit(finalUrl, true);
          } else {
            await swapPageFromHtml(html, finalUrl, true, previousScroll);
          }
          syncTopCartCounter();
          showToast((form.action || '').includes('/pedido') ? 'Adicionado com sucesso.' : 'Atualizado com sucesso.');
        } catch (err) {
          showToast('Não foi possível concluir a ação.');
          window.location.reload();
        } finally {
          form.dataset.submitting = '0';
          setButtonLoading(button, false);
        }
      });
    });
  }

  function bindCopyPix() {
    document.querySelectorAll('.js-copy-pix,[data-copy-target]').forEach((button) => {
      if (button.dataset.copyBound === '1') return;
      button.dataset.copyBound = '1';
      button.addEventListener('click', async (event) => {
        event.preventDefault();
        const targetSel = button.getAttribute('data-copy-target');
        const target = targetSel ? document.querySelector(targetSel) : null;
        const value = button.getAttribute('data-copy-value') || (target ? target.value : '');
        try {
          await copyPix(value, target);
          showToast('Chave Pix copiada.');
        } catch (e) {
          window.prompt('Copie sua chave Pix:', value);
          showToast('A cópia automática falhou. Use a chave exibida.');
        }
      });
    });
  }


  function scheduleAutoSubmit(form) {
    if (!form || form.dataset.submitting === '1') return;
    const delay = Number(form.dataset.autoSubmitDelay || 160);
    window.clearTimeout(Number(form.dataset.autoSubmitTimer || 0));
    const timer = window.setTimeout(() => {
      if (form.dataset.submitting === '1') return;
      try {
        if (typeof form.requestSubmit === 'function') form.requestSubmit();
        else form.submit();
      } catch (e) {
        form.submit();
      }
    }, delay);
    form.dataset.autoSubmitTimer = String(timer);
  }

  function bindExternalSubmitButtons() {
    document.querySelectorAll('[data-submit-external-form]').forEach((button) => {
      if (button.dataset.externalSubmitBound === '1') return;
      button.dataset.externalSubmitBound = '1';
      button.addEventListener('click', () => {
        const selector = button.getAttribute('data-submit-external-form');
        const form = selector ? document.querySelector(selector) : null;
        if (!form) return;
        if (typeof form.requestSubmit === 'function') form.requestSubmit();
        else form.submit();
      });
    });
  }

  function bindQuantityControls() {
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
        if (next === current) return;
        input.value = next;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        const form = input.closest('form[data-auto-submit="quantity"]');
        if (form) scheduleAutoSubmit(form);
      });
    });

    document.querySelectorAll('form[data-auto-submit="quantity"] [data-quantity-input]').forEach((input) => {
      if (input.dataset.autoSubmitBound === '1') return;
      input.dataset.autoSubmitBound = '1';
      input.addEventListener('input', () => {
        const form = input.closest('form[data-auto-submit="quantity"]');
        if (form) scheduleAutoSubmit(form);
      });
      input.addEventListener('change', () => {
        const form = input.closest('form[data-auto-submit="quantity"]');
        if (form) scheduleAutoSubmit(form);
      });
    });
  }

  function bindCheckoutWhatsapp() {
    document.querySelectorAll('.js-open-whatsapp-before-pay').forEach((link) => {
      if (link.dataset.whatsBound === '1') return;
      link.dataset.whatsBound = '1';
      link.addEventListener('click', function () {
        const whatsapp = link.dataset.whatsapp;
        if (whatsapp) {
          try { window.open(whatsapp, '_blank'); } catch (e) {}
        }
      });
    });
  }

  function bindCarousel() {
    const carrossel = document.getElementById('carrosselProdutos');
    const prevBtn = document.getElementById('prevCard');
    const nextBtn = document.getElementById('nextCard');
    if (!carrossel) return;
    const getScrollAmount = () => {
      const card = carrossel.querySelector('.card-carrossel');
      if (!card) return 320;
      const style = window.getComputedStyle(carrossel);
      const gap = parseFloat(style.columnGap || style.gap || 20);
      return card.offsetWidth + gap;
    };
    if (prevBtn && prevBtn.dataset.bound !== '1') {
      prevBtn.dataset.bound = '1';
      prevBtn.addEventListener('click', () => carrossel.scrollBy({ left: -getScrollAmount(), behavior: 'smooth' }));
    }
    if (nextBtn && nextBtn.dataset.bound !== '1') {
      nextBtn.dataset.bound = '1';
      nextBtn.addEventListener('click', () => carrossel.scrollBy({ left: getScrollAmount(), behavior: 'smooth' }));
    }
  }

  function bind() {
    bindLinks();
    bindForms();
    bindCopyPix();
    bindExternalSubmitButtons();
    bindQuantityControls();
    bindCheckoutWhatsapp();
    bindCarousel();
    syncTopCartCounter();
  }

  window.addEventListener('popstate', () => {
    visit(window.location.href, false).catch(() => window.location.reload());
  });

  bind();
  window.SiteAjax = { showToast, visit, bind, copyPix };
})();
