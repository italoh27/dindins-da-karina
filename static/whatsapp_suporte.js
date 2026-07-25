(() => {
  const widgets = document.querySelectorAll('[data-whatsapp-suporte]');

  widgets.forEach((widget) => {
    if (widget.dataset.iniciado === '1') return;
    widget.dataset.iniciado = '1';

    const painel = widget.querySelector('.whatsapp-suporte-painel');
    const abrir = widget.querySelector('[data-whatsapp-abrir]');
    const fechar = widget.querySelector('[data-whatsapp-fechar]');
    const enviar = widget.querySelector('[data-whatsapp-enviar]');
    const assunto = widget.querySelector('[data-whatsapp-assunto]');
    const mensagem = widget.querySelector('[data-whatsapp-mensagem]');

    const alternar = (aberto) => {
      painel.hidden = !aberto;
      abrir.setAttribute('aria-expanded', aberto ? 'true' : 'false');
      widget.classList.toggle('aberto', aberto);
      if (aberto) assunto.focus();
    };

    abrir.addEventListener('click', () => alternar(painel.hidden));
    fechar.addEventListener('click', () => alternar(false));

    enviar.addEventListener('click', () => {
      const numero = String(widget.dataset.numero || '').replace(/\D/g, '');
      if (!numero) return;

      const detalhe = String(mensagem.value || '').trim();
      const pagina = document.title ? `Página: ${document.title}.` : '';
      const texto = [
        'Olá! Vim pelo aplicativo Dindins da Karina.',
        assunto.value,
        detalhe,
        pagina
      ].filter(Boolean).join('\n\n');

      window.open(`https://wa.me/${numero}?text=${encodeURIComponent(texto)}`, '_blank', 'noopener');
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !painel.hidden) alternar(false);
    });
  });
})();
