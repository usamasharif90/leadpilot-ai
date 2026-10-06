
function openModal(id){document.getElementById(id)?.classList.add('open')}
function closeModal(id){document.getElementById(id)?.classList.remove('open')}
function fmt(n){n=Number(n||0);return n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(n>=1e5?0:1)+'K':String(n)}
async function showLead(id){
 const d=await fetch('/api/lead/'+id).then(r=>r.json());
 document.getElementById('leadName').textContent=d.name;
 document.getElementById('leadSubtitle').textContent=`${d.platform} · ${d.niche} · ${d.region}`;
 document.getElementById('leadBody').innerHTML=`<div class="mini-grid"><div class="mini"><small>Audience</small><b>${fmt(d.followers)}</b></div><div class="mini"><small>30-day views</small><b>${fmt(d.views_30d)}</b></div><div class="mini"><small>Coaching confidence</small><b>${Math.round((d.coaching_confidence||0)*100)}%</b></div><div class="mini"><small>Lead score</small><b>${d.score}/100</b></div></div><h4>Qualification evidence</h4><div class="mini">${d.evidence||'Qualified by the configured ICP filters.'}</div><h4>Contact</h4><div class="mini">${d.email||'No public email found'}<br>${d.ig_handle||'Instagram profile not matched'}<br>${d.profile_url||''}</div>`;
 openModal('leadModal');
}



function jobActionHtml(j){
  if(j.status==='completed'){
    return `<div class="ds-actions">
      <a class="btn small" href="/finder?run_id=${j.id}">View leads</a>
      <a class="btn small green" href="/jobs/${j.id}/export.xlsx">Download Excel</a>
    </div>`;
  }
  if(j.status==='failed') return `<span class="ds-muted">Check error</span>`;
  if(j.status==='needs_credentials') return `<span class="ds-muted">Credentials required</span>`;
  return `<span class="ds-muted">Processing...</span>`;
}

let jobPollTimer=null;

async function pollDiscoveryJobs(){
  const table=document.getElementById('jobsTable');
  if(!table) return;

  let hasActive=false;

  try{
    const response=await fetch('/api/jobs?ts='+Date.now(),{
      cache:'no-store',
      headers:{'Cache-Control':'no-cache'}
    });
    const jobs=await response.json();

    for(const j of jobs){
      if(['queued','running'].includes(j.status)) hasActive=true;

      const row=table.querySelector(`[data-job-id="${j.id}"]`);
      if(!row) continue;

      const status=row.querySelector('.job-status');
      const bar=row.querySelector('.job-progress');
      const track=row.querySelector('.ds-progress-track');
      const label=row.querySelector('.progress-label');
      const message=row.querySelector('.job-message');
      const results=row.querySelector('.job-results');
      const actions=row.querySelector('.job-actions');

      const pct=Math.max(0,Math.min(100,Number(j.progress||0)));

      if(status){
        status.textContent=j.status;
        status.className='badge job-status '+(
          j.status==='completed' ? 'b-green' :
          j.status==='failed' ? 'b-red' : 'b-yellow'
        );
      }

      if(bar) bar.style.width=pct+'%';
      if(track) track.classList.toggle('running',['queued','running'].includes(j.status));
      if(label) label.textContent=pct+'%';
      if(message) message.textContent=j.message||'';
      if(results) results.textContent=j.result_count||0;
      if(actions) actions.innerHTML=jobActionHtml(j);
    }

  }catch(err){
    console.warn('Live job update failed',err);
    hasActive=true;
  }

  clearTimeout(jobPollTimer);

  if(hasActive){
    jobPollTimer=setTimeout(pollDiscoveryJobs,1200);
  }
}

document.addEventListener('DOMContentLoaded',()=>{
  const table=document.getElementById('jobsTable');
  if(!table) return;

  const active=[...table.querySelectorAll('.job-status')]
    .some(el=>['queued','running'].includes(el.textContent.trim()));

  if(active) pollDiscoveryJobs();
});



