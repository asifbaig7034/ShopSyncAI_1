const $ = id => document.getElementById(id);
let token = sessionStorage.getItem('shopsync_owner_token') || '';
let state = {products:[], orders:[], finance:{booked_net_cents:0}, audit:[], notifications:[]};
$('token').value = token;
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = cents => 'PKR ' + (cents / 100).toLocaleString('en-PK',{minimumFractionDigits:2,maximumFractionDigits:2});
const requestId = () => crypto.randomUUID ? crypto.randomUUID() : String(Date.now())+Math.random();

function notice(message, error=false){let el=$('notice');el.hidden=false;el.className='notice'+(error?' error':'');el.textContent=message;clearTimeout(notice.timer);notice.timer=setTimeout(()=>el.hidden=true,7000);}
async function api(path, options={}){
  let r=await fetch(path,{...options,headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json',...(options.headers||{})}});
  let body=await r.json().catch(()=>({detail:'Unexpected server response'}));
  if(!r.ok)throw new Error(typeof body.detail==='string'?body.detail:JSON.stringify(body.detail));
  return body;
}
const post=(path,data={})=>api(path,{method:'POST',body:JSON.stringify(data)});
async function refresh(){
  try{
    let [products,orders,finance,audit,notifications]=await Promise.all([
      api('/api/products'),api('/api/orders'),api('/api/finance'),api('/api/audit'),api('/api/notifications')]);
    state={products,orders,finance,audit,notifications};render();
  }catch(err){notice(err.message,true);}
}
function render(){
  $('m-orders').textContent=state.orders.length;
  $('m-holds').textContent=state.orders.filter(o=>o.status==='RISK_HOLD').length;
  $('m-returns').textContent=state.orders.filter(o=>o.return_request&&['PENDING','APPROVED_AWAITING_RECEIPT'].includes(o.return_request.status)).length;
  $('m-sales').textContent=money(state.finance.booked_net_cents);
  $('order-count').textContent=state.orders.length+' total';
  $('product-select').innerHTML='<option value="">Select product</option>'+state.products.filter(p=>p.active).map(p=>`<option value="${escapeHtml(p.id)}">${escapeHtml(p.sku)} · ${escapeHtml(p.name)} · ${money(p.price_cents)} · ${p.stock_qty} available</option>`).join('');
  $('product-list').innerHTML=state.products.length?state.products.map(p=>`<div class="row"><div class="row-top"><strong>${escapeHtml(p.name)} <span class="small">${escapeHtml(p.sku)}</span></strong><span class="badge">${p.stock_qty} in stock</span></div><div class="order-meta">${money(p.price_cents)}</div><div class="actions"><button data-action="restock" data-id="${escapeHtml(p.id)}">Restock</button></div></div>`).join(''):'<p class="empty">Add a product to start.</p>';
  $('order-list').innerHTML=state.orders.length?state.orders.map(orderCard).join(''):'<p class="empty">No orders yet. Create a draft or try the chat simulator.</p>';
  $('audit').innerHTML=state.audit.length?state.audit.slice(0,30).map(a=>`<div class="row"><strong>${escapeHtml(a.event.replaceAll('_',' '))}</strong><div class="small">${escapeHtml(a.actor)} · ${escapeHtml(a.order_id||'catalog')} · ${escapeHtml(a.created_at)}</div></div>`).join(''):'<p class="empty">No actions recorded.</p>';
  $('notifications').innerHTML=state.notifications.length?state.notifications.map(n=>`<div class="row"><div class="row-top"><strong>${escapeHtml(n.phone)}</strong><span class="badge ${escapeHtml(n.status)}">${escapeHtml(n.status)}</span></div><div class="order-meta">${escapeHtml(n.body)}</div><div class="small">${escapeHtml(n.last_error||'')} · ${n.attempts} attempts</div>${['FAILED','MANUAL_REVIEW','NEEDS_CONFIGURATION','NEEDS_TEMPLATE','UNCERTAIN'].includes(n.status)?`<div class="actions"><button data-action="retry" data-id="${escapeHtml(n.id)}">Review and retry</button></div>`:''}</div>`).join(''):'<p class="empty">No delivery notifications.</p>';
}
function orderCard(o){
  let ret=o.return_request;
  let actions=[];
  if(o.status==='DRAFT')actions.push(['edit','Edit draft'],['confirm','Confirm order']);
  if(o.status==='RISK_HOLD')actions.push(['approve-risk','Approve risk'],['reject-risk','Reject risk']);
  if(['DRAFT','RISK_HOLD','CONFIRMED'].includes(o.status))actions.push(['cancel','Cancel']);
  if(o.status==='CONFIRMED')actions.push(['ship','Mark shipped']);
  if(['CONFIRMED','SHIPPED'].includes(o.status)&&!ret)actions.push(['return','Request return'],['refund','Request refund']);
  if(ret?.status==='PENDING')actions.push(['approve-return','Approve '+ret.kind.toLowerCase()],['reject-return','Reject request']);
  if(ret?.status==='APPROVED_AWAITING_RECEIPT')actions.push(['accept-item','Item accepted'],['reject-item','Item rejected']);
  return `<article class="order-card"><div class="order-top"><div><strong>#${escapeHtml(o.id)}</strong><div class="order-meta">${escapeHtml(o.customer_name)} · ${escapeHtml(o.customer_phone)} · ${escapeHtml(o.channel)} · ${escapeHtml(o.created_at)}</div></div><span class="badge ${escapeHtml(o.status)}">${escapeHtml(o.status.replaceAll('_',' '))}</span></div><div class="order-items">${o.items.map(i=>`${i.quantity} × ${escapeHtml(i.name)} (${escapeHtml(i.sku)})`).join(', ')} · <strong>${money(o.total_cents)}</strong></div>${o.risk_score!==null?`<div class="subdetail">Risk review: ${o.risk_score}/100 · ${o.risk_reasons.map(escapeHtml).join('; ')}</div>`:''}${ret?`<div class="subdetail">${escapeHtml(ret.kind)} request: ${escapeHtml(ret.status.replaceAll('_',' '))} · ${escapeHtml(ret.reason)}</div>`:''}<div class="actions">${actions.map(([action,label])=>`<button data-action="${action}" data-id="${escapeHtml(o.id)}">${label}</button>`).join('')}</div></article>`;
}

$('connect').onclick=async()=>{token=$('token').value.trim();sessionStorage.setItem('shopsync_owner_token',token);await refresh();};
$('refresh').onclick=refresh;
document.querySelectorAll('.tabs button').forEach(btn=>btn.onclick=()=>{document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('active',x===btn));document.querySelectorAll('.tab-panel').forEach(x=>x.hidden=x.id!==btn.dataset.tab);});
$('product-form').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target);try{await post('/api/products',{sku:f.get('sku'),name:f.get('name'),price_cents:Math.round(Number(f.get('price'))*100),stock_qty:Number(f.get('stock_qty'))});e.target.reset();notice('Product added.');await refresh();}catch(err){notice(err.message,true);}};
$('draft-form').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target);try{let o=await post('/api/orders/drafts',{phone:f.get('phone'),name:f.get('name'),delivery_address:f.get('delivery_address'),payment_method:f.get('payment_method'),items:[{product_id:f.get('product_id'),quantity:Number(f.get('quantity'))}],source_key:requestId()});notice(`Draft ${o.id} created. Confirm it when the customer agrees.`);e.target.reset();await refresh();}catch(err){notice(err.message,true);}};
$('chat-form').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target);let text=String(f.get('text'));try{let answer=await post('/api/chat/simulate',{message_id:requestId(),phone:f.get('phone'),name:f.get('name'),text});let log=$('chat-log');if(log.querySelector('.empty'))log.innerHTML='';for(let [who,body] of [['customer',text],['ShopSync',answer.reply]]){let div=document.createElement('div');div.className='bubble '+(who==='customer'?'customer':'');let label=document.createElement('small');label.textContent=who+' · '+f.get('phone');div.append(label,document.createTextNode(body));log.appendChild(div);}log.scrollTop=log.scrollHeight;e.target.querySelector('textarea').value='';await refresh();}catch(err){notice(err.message,true);}};
document.addEventListener('click',async e=>{let btn=e.target.closest('button[data-action]');if(!btn)return;let action=btn.dataset.action,id=btn.dataset.id,o=state.orders.find(x=>x.id===id);let path='',body={};try{
  if(action==='restock'){let q=prompt('How many units to add?');if(q===null)return;path=`/api/products/${id}/restock`;body={quantity:Number(q)};}
  else if(action==='retry'){if(!confirm('Retry delivery? Check whether the customer already received it first.'))return;path=`/api/notifications/${id}/retry`;}
  else if(action==='edit'){
    let product=prompt('Product SKU:',o.items[0].sku);if(product===null)return;
    let selected=state.products.find(p=>p.sku===product.trim().toUpperCase());if(!selected)throw new Error('Unknown SKU.');
    let quantity=prompt('Quantity:',o.items[0].quantity);if(quantity===null)return;
    let address=prompt('Delivery address:',o.delivery_address);if(address===null)return;
    btn.disabled=true;await api(`/api/orders/${id}/draft`,{method:'PATCH',body:JSON.stringify({items:[{product_id:selected.id,quantity:Number(quantity)}],delivery_address:address})});notice('Draft updated.');await refresh();return;
  }
  else if(action==='confirm')path=`/api/orders/${id}/confirm`;
  else if(action==='cancel'){if(!confirm('Cancel this order?'))return;path=`/api/orders/${id}/cancel`;}
  else if(action==='ship')path=`/api/orders/${id}/ship`;
  else if(action==='approve-risk'||action==='reject-risk'){let reason=prompt('Decision note (optional):');if(reason===null)return;path=`/api/orders/${id}/risk-decision`;body={approve:action==='approve-risk',reason};}
  else if(action==='return'||action==='refund'){let reason=prompt('Customer reason for request:');if(!reason)return;path=`/api/orders/${id}/returns`;body={kind:action.toUpperCase(),reason};}
  else if(action==='approve-return'||action==='reject-return'){let reason=prompt('Decision note:');if(reason===null)return;path=`/api/returns/${o.return_request.id}/decision`;body={approve:action==='approve-return',reason};}
  else if(action==='accept-item'||action==='reject-item'){path=`/api/returns/${o.return_request.id}/inspect`;body={accept:action==='accept-item'};}
  if(!path)return;btn.disabled=true;await post(path,body);notice('Action saved.');await refresh();
}catch(err){notice(err.message,true);}finally{btn.disabled=false;}});
if(token)refresh();
