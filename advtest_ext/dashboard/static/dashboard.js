/* Interactions shared by the reference dashboard. All content is text-safe. */
(function () {
  'use strict';
  function read(key) { try { return JSON.parse(localStorage.getItem('fission-' + key) || '{}'); } catch (_) { return {}; } }
  function profile() {
    var user = read('profile');
    var initials = (user.name || 'Fission').trim().split(/\s+/).slice(0, 2).map(function (n) { return n[0]; }).join('').toUpperCase();
    document.querySelectorAll('[data-profile-initials]').forEach(function (el) { el.textContent = initials; });
  }
  profile();
  document.querySelectorAll('.searchbox').forEach(function(box){
    if(box.querySelector('svg'))return;
    var svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 16 16');svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.3');svg.setAttribute('aria-hidden','true');
    var circle=document.createElementNS(svg.namespaceURI,'circle');circle.setAttribute('cx','7');circle.setAttribute('cy','7');circle.setAttribute('r','4.5');
    var path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d','m10.5 10.5 3.5 3.5');svg.append(circle,path);box.prepend(svg);
  });
  // Keep wide evidence and evaluation tables reachable on small screens.
  document.querySelectorAll('main table').forEach(function(table){
    if(table.closest('.table-scroll'))return;
    var wrapper=document.createElement('div');wrapper.className='table-scroll';
    table.parentNode.insertBefore(wrapper,table);wrapper.appendChild(table);
  });
  var serviceMenu = document.querySelector('.workspace-switcher');
  document.addEventListener('click', function(event) {
    if (serviceMenu && !serviceMenu.contains(event.target)) serviceMenu.open = false;
  });
  document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape' && serviceMenu && serviceMenu.open) {
      serviceMenu.open = false;
      serviceMenu.querySelector('summary').focus();
    }
  });
  var mobile = document.querySelector('.mobile-nav');
  if (mobile) mobile.addEventListener('click', function () { this.setAttribute('aria-expanded', String(document.body.classList.toggle('nav-open'))); });
  document.addEventListener('keydown', function(e) { if(e.key === 'Escape' && mobile) {document.body.classList.remove('nav-open');mobile.setAttribute('aria-expanded','false');} });
  var active = Array.from(document.querySelectorAll('.nav-item[data-path]')).filter(function (a) { return a.dataset.path !== '/' && (location.pathname === a.dataset.path || location.pathname.startsWith(a.dataset.path + '/')); }).sort(function (a, b) { return b.dataset.path.length - a.dataset.path.length; })[0];
  if (active) {
    document.querySelectorAll('.nav-item.active').forEach(function (a) { a.classList.remove('active'); a.removeAttribute('aria-current'); });
    active.classList.add('active'); active.setAttribute('aria-current', 'page');
    var group = active.closest('.nav-group'); if (group) group.classList.remove('collapsed');
  }
  document.querySelectorAll('.nav-group > button').forEach(function(button){
    button.setAttribute('aria-expanded',String(!button.parentElement.classList.contains('collapsed')));
    button.addEventListener('click',function(){button.setAttribute('aria-expanded',String(!button.parentElement.classList.contains('collapsed')));});
  });
  function selectTab(button) {
    var group = button.closest('[data-tab-group]');
    group.querySelectorAll('[data-tab]').forEach(function (tab) {
      if (tab.closest('[data-tab-group]') !== group) return;
      var selected = tab === button; tab.classList.toggle('on', selected); tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
    });
    group.querySelectorAll('[data-tab-panel]').forEach(function (panel) { if (panel.closest('[data-tab-group]') === group) panel.hidden = panel.id !== button.dataset.tab; });
  }
  document.querySelectorAll('[data-tab-group]').forEach(function(group){var tab=Array.from(group.querySelectorAll('[data-tab]')).find(function(t){return t.closest('[data-tab-group]')===group;});if(tab)selectTab(tab);});
  document.addEventListener('click', function (event) { var tab = event.target.closest('[data-tab]'); if (tab) selectTab(tab); });
  document.addEventListener('keydown', function(event) {
    var tab=event.target.closest('[data-tab]'); if(!tab || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    var tabs=Array.from(tab.parentElement.querySelectorAll('[data-tab]')),index=tabs.indexOf(tab);
    index=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
    event.preventDefault();selectTab(tabs[index]);tabs[index].focus();
  });
  document.addEventListener('change', function(event) {
    if(event.target.matches('[data-select-all]'))event.target.closest('table').querySelectorAll('tbody tr:not([hidden]) .rowcheck').forEach(function(box){box.checked=event.target.checked;});
  });
  document.querySelectorAll('[data-table-search]').forEach(function(input){input.addEventListener('input',function(){var q=input.value.toLowerCase();document.querySelectorAll('#'+input.dataset.tableSearch+' tbody tr').forEach(function(row){row.hidden=!row.textContent.toLowerCase().includes(q);});});});
  document.querySelectorAll('form[data-preferences]').forEach(function (form) {
    var key = form.dataset.preferences, saved = read(key), pendingImage = saved.image || '';
    Object.keys(saved).forEach(function (name) { var field = form.elements.namedItem(name); if (field && name !== 'image') field.value = saved[name]; });
    var preview = form.querySelector('[data-image-preview]'), upload = form.querySelector('[data-image-upload]'), status = form.querySelector('[role=status]');
    function showImage(src) { if(!preview || !src.startsWith('data:image/'))return;var img=document.createElement('img');img.src=src;img.alt=key==='profile'?'Profile photo':'Workspace logo';preview.replaceChildren(img); }
    if (pendingImage) showImage(pendingImage);
    if (upload) upload.addEventListener('change', function () {
      var file=this.files[0];if(!file)return;
      if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>2*1024*1024){status.textContent='Choose a PNG, JPG or WebP image up to 2 MB.';return;}
      var reader=new FileReader();reader.onload=function(){pendingImage=String(reader.result);showImage(pendingImage);status.textContent='Image ready. Save changes to keep it.';};reader.readAsDataURL(file);
    });
    form.addEventListener('submit', function (event) {
      event.preventDefault();var data=Object.fromEntries(new FormData(form));data.image=pendingImage;
      try {localStorage.setItem('fission-'+key,JSON.stringify(data));status.textContent='Changes saved in this browser.';profile();}
      catch (_) {status.textContent='Could not save preferences. Browser storage may be full or unavailable.';}
    });
  });
})();
