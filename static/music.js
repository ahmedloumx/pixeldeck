"use strict";

/* The Music panel: a retro player for Spotify, and its mini player in the status bar. */

const Music = (() => {
  function mount(el) {
    el.innerHTML = '<p class="empty">The music player lands here.</p>';
  }
  return { mount };
})();
