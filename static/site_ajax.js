
// CORRIGIDO - UX carrinho profissional
(function () {

  function showToast(text) {
    const toast = document.getElementById('toastGlobal');
    if (!toast) return;
    toast.textContent = text;
    toast.hidden = false;
    toast.classList.add('mostrar');
    setTimeout(() => {
      toast.classList.remove('mostrar');
      setTimeout(() => { toast.hidden = true; }, 250);
    }, 2000);
  }

  function handleJsonFormSuccess(form, payload) {
    const action = form.getAttribute('action') || '';

    if (action.indexOf('/remover_item/') === 0) {
      updateCartItemDom(payload);
      showToast('Removido com sucesso.');
      return;
    }

    if (action.indexOf('/carrinho/atualizar/') === 0) {
      updateCartItemDom(payload);
      return; // 🔥 SEM TOAST
    }

    if (action === '/pedido') {
      showToast('Adicionado com sucesso.');
      return;
    }
  }

  function bindForms() {
    document.querySelectorAll('form.js-ajax-form').forEach((form) => {
      if (form.dataset.ajaxBound === '1') return;
      form.dataset.ajaxBound = '1';

      form.addEventListener('submit', async (e) => {
        e.preventDefault();

        try {
          const formData = new FormData(form);

          const response = await fetch(form.action, {
            method: form.method || 'POST',
            body: formData,
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
          });

          const contentType = response.headers.get('content-type') || '';

          if (contentType.includes('application/json')) {
            const payload = await response.json();

            const action = form.getAttribute('action') || '';
            const isCarrinho =
              action.includes('/remover_item/') ||
              action.includes('/carrinho/atualizar/');

            if (!response.ok || payload.ok === false) {
              if (isCarrinho) {
                // 🔥 NÃO MOSTRA ERRO, só sincroniza
                location.reload();
                return;
              }

              showToast(payload.message || 'Erro');
              return;
            }

            handleJsonFormSuccess(form, payload);
            return;
          }

        } catch (err) {
          const action = form.getAttribute('action') || '';

          if (
            action.includes('/remover_item/') ||
            action.includes('/carrinho/atualizar/')
          ) {
            location.reload();
            return;
          }

          showToast('Erro na ação');
        }
      });
    });
  }

  function updateCartItemDom(payload) {
    if (!payload) return;

    if (payload.removed_index !== undefined) {
      const el = document.querySelector('[data-cart-item="' + payload.removed_index + '"]');
      if (el) el.remove();
    }
  }

  function bind() {
    bindForms();
  }

  bind();

})();
