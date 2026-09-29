/* 后台公共头部渲染（零阻塞即时挂载 + 悬停极速预取） */
let _cachedVersion=sessionStorage.getItem('zcode_hub_version')||'';

function renderAdminHeader(){
  const mount=document.getElementById('admin-header');
  if(!mount)return;
  const active=mount.dataset.active||location.pathname;
  const nav=[
    ['/admin/accounts','账号池'],
    ['/admin/monitoring','请求监控'],
    ['/admin/settings','设置'],
  ].map(([href,label])=>
    `<a href="${href}" class="admin-nav-link${href===active?' active':''}">${label}</a>`
  ).join('');
  // 优先取服务端注入的真实版本号，覆盖并校准 sessionStorage 中的旧版本缓存
  const serverVer=(mount.dataset.version||'').trim();
  if(serverVer&&!serverVer.includes('{{')){
    _cachedVersion=serverVer;
    try{sessionStorage.setItem('zcode_hub_version',_cachedVersion);}catch(e){}
  }

  mount.innerHTML=`
    <header class="admin-header">
      <div class="admin-header-inner">
        <div class="admin-brand-wrap">
          <span class="admin-brand">Z<span class="tick">·</span>HUB</span>
        </div>
        <nav class="admin-nav">${nav}</nav>
        <div class="admin-header-right">
          <span class="admin-header-version" id="header-version" style="${_cachedVersion?'':'display:none'}">${_cachedVersion}</span>
          <button onclick="adminLogout()" class="admin-header-icon-btn" title="退出登录" aria-label="退出登录">
            <svg viewBox="0 0 24 24"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/></svg>
          </button>
        </div>
      </div>
    </header>`;

  // 始终异步向 /meta 校准最新版本，发现更新立即热刷新，杜绝 sessionStorage 锁死旧版本
  fetch('/meta?t='+Date.now()).then(r=>r.ok?r.json():null).then(d=>{
    if(d&&d.version){
      const latest='v'+d.version;
      if(latest!==_cachedVersion){
        _cachedVersion=latest;
        try{sessionStorage.setItem('zcode_hub_version',_cachedVersion);}catch(e){}
        const el=document.getElementById('header-version');
        if(el){el.textContent=_cachedVersion;el.style.display='';}
      }
    }
  }).catch(()=>{});

  initNavPrefetch();
}

/* 导航链接悬停预拉取（Hover Prefetch）：提前将目标页面载入缓存，实现秒开 */
function initNavPrefetch(){
  const links=document.querySelectorAll('.admin-nav-link');
  links.forEach(a=>{
    const href=a.getAttribute('href');
    if(!href||href===location.pathname)return;
    const doPrefetch=()=>{
      if(a._prefetched)return;
      a._prefetched=true;
      const link=document.createElement('link');
      link.rel='prefetch';
      link.href=href;
      document.head.appendChild(link);
    };
    a.addEventListener('mouseenter',doPrefetch,{once:true});
    a.addEventListener('touchstart',doPrefetch,{once:true,passive:true});
  });
}
