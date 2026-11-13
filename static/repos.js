"use strict";

/* The Repos panel: the project's repositories, their branches and their code. */

const Repos = (() => {
  function mount(el) {
    el.innerHTML = '<p class="empty">Repositories land here.</p>';
  }
  return { mount };
})();
