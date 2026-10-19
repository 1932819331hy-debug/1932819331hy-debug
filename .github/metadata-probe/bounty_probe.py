#!/usr/bin/env python3
"""Read-only, non-LLM metadata probe. SQLite is DERIVED, never a task ledger.
No credentials, claim, dispatch, GitHub write or model invocation is implemented.
macOS system curl keeps OS TLS verification; Python installation's CA failed.
"""
import argparse,datetime,hashlib,json,os,sqlite3,subprocess,tempfile,time,uuid
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode

def utc(t): return datetime.datetime.fromtimestamp(t,datetime.timezone.utc).isoformat()
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def fetch(source,headers):
    if not source['url'].startswith('https://'): raise ValueError('Production sources must use verified HTTPS')
    with tempfile.TemporaryDirectory() as folder:
        h=Path(folder)/'headers'; b=Path(folder)/'body'
        args=['/usr/bin/curl','--silent','--show-error','--location','--proto','=https','--proto-redir','=https','--max-time','25','--max-filesize','4194304','--user-agent','bounty-ops-v4-readonly/1','--dump-header',str(h),'--output',str(b),'--write-out','%{http_code}']
        for key,value in headers.items(): args.extend(['--header',key+': '+value])
        result=subprocess.run(args+[source['url']],capture_output=True,text=True,timeout=30)
        if result.returncode: raise RuntimeError('curl '+str(result.returncode)+': '+result.stderr[:350])
        blocks=h.read_text().replace('\r','').strip().split('\n\n'); parsed={}
        for line in blocks[-1].splitlines()[1:]:
            if ':' in line:
                k,v=line.split(':',1); parsed[k.lower()]=v.strip()
        return int(result.stdout),parsed,b.read_bytes() if b.exists() else b''

def normalize(source,data):
    kind=source['kind']; items=[]
    if kind=='squash':
        if not isinstance(data,dict) or not isinstance(data.get('jobs'),list): raise ValueError('Missing jobs list')
        raw=data['jobs']; total=data.get('count',len(raw))
        for x in raw:
            identity=x['spec_id']
            semantic={k:x.get(k) for k in ['spec_id','repo_url','amount_minor','currency','state','internal']}
            items.append((identity,semantic))
    elif kind=='github_search':
        if not isinstance(data,dict) or not isinstance(data.get('items'),list): raise ValueError('Missing items list')
        if data.get('incomplete_results'): raise ValueError('GitHub incomplete_results; retain prior coverage')
        raw=data['items']; total=data['total_count']
        for x in raw:
            if source.get('account') and x['user']['login']!=source['account']: continue
            # updated_at alone (including own edits) is deliberately not a signal.
            semantic={k:x.get(k) for k in ['id','html_url','state','comments','title']}
            semantic['author']=x['user']['login']; items.append(('gh:'+str(x['id']),semantic))
    elif kind=='github_pulls':
        if not isinstance(data,list): raise ValueError('Expected PR list')
        raw=data; total=len(raw)
        for x in raw:
            if x['user']['login']!=source['account']: continue
            semantic={'id':x['id'],'html_url':x['html_url'],'state':x['state'],'head':x['head']['sha'],'draft':x['draft']}
            items.append(('gh:'+str(x['id']),semantic))
    else: raise ValueError('Unsupported source parser')
    return items,{'returned':len(raw),'total':total,'saturated':len(raw)>=source.get('limit',100) or total>len(raw)}

def run(db,manifest,trigger,now=None,transport=fetch):
    start=time.time() if now is None else now
    Path(db).parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(db,timeout=1)
    con.executescript('''CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items(source TEXT,id TEXT,fingerprint TEXT,PRIMARY KEY(source,id));
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,source TEXT,item TEXT,payload TEXT,observed REAL);
CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,created REAL,payload TEXT);''')
    # Transaction serializes this local derived DB only, never cross-host ownership.
    con.execute('BEGIN IMMEDIATE')
    prior=con.execute('SELECT id FROM receipts ORDER BY created DESC,rowid DESC LIMIT 1').fetchone()
    receipt={'id':'probe-'+uuid.uuid4().hex,'trigger':trigger,'pid':os.getpid(),'parent_pid':os.getppid(),'program_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'manifest_sha256':digest(manifest),'start':utc(start),'prior_receipt_id':prior[0] if prior else None,'canonical_page':manifest['canonical_page'],'cache_role':'DERIVED_NO_DISPATCH_AUTHORITY','sources':[],'new_events':0,'model_calls':0,'public_writes':0,'executor_delivery':'NOT_CONFIGURED','host':'local macOS launchd/manual; no cloud claim'}
    try:
        for source in manifest['sources'][:4]:
            row=con.execute('SELECT state FROM sources WHERE id=?',(source['id'],)).fetchone(); state=json.loads(row[0]) if row else {}
            result={'source':source['id']}
            if start<state.get('next_due',0):
                result.update(status='NOT_DUE',next_due=utc(state['next_due'])); receipt['sources'].append(result); continue
            headers={}
            if state.get('etag'): headers['If-None-Match']=state['etag']
            if state.get('last_modified'): headers['If-Modified-Since']=state['last_modified']
            partition=state.get('partition',0); chosen=dict(source)
            if source.get('urls'): chosen['url']=source['urls'][partition%len(source['urls'])]
            page=state.get('pages',{}).get(str(partition % len(source.get('urls',[source['url']]))),1)
            if source['kind'].startswith('github_'):
                parts=urlsplit(chosen['url']); query=dict(parse_qsl(parts.query)); query['page']=str(page)
                chosen['url']=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),parts.fragment))
            h={}
            try:
                status,h,body=transport(chosen,headers); result.update(http_status=status,url=chosen['url'])
                if status==304:
                    if not state.get('last_success'): raise ValueError('304 without cached successful representation')
                    result.update(status='NOT_MODIFIED',saturated=state.get('saturated',False))
                elif status==200:
                    items,coverage=normalize(source,json.loads(body)); result.update(status='OK',page=page,partition=partition % len(source.get('urls',[source['url']])),**coverage)
                    baseline=not state.get('last_success')
                    for identity,semantic in items:
                        fingerprint=digest(semantic); old=con.execute('SELECT fingerprint FROM items WHERE source=? AND id=?',(source['id'],identity)).fetchone()
                        if not old or old[0]!=fingerprint:
                            event_id=digest([source['id'],identity,fingerprint])
                            payload={'canonical_id':identity,'data':semantic,'kind':'BASELINE_UNTRIAGED' if baseline else 'METADATA_CHANGE_REQUIRES_HUMAN_VERIFICATION','builder':'UNKNOWN','publisher':'UNKNOWN','dispatch_allowed':False,'publish_allowed':False}
                            c=con.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)',(event_id,source['id'],identity,json.dumps(payload),start)); receipt['new_events']+=c.rowcount
                            con.execute('INSERT OR REPLACE INTO items VALUES(?,?,?)',(source['id'],identity,fingerprint))
                    state.update(last_success=start,coverage=coverage,saturated=coverage['saturated'])
                    # Every rotating partition needs its own conditional representation.
                    state['partition']=partition+1
                    more=coverage['returned']>=source.get('limit',100) and (source['kind']=='github_pulls' or coverage['total']>page*source.get('limit',100))
                    state.setdefault('pages',{})[str(partition % len(source.get('urls',[source['url']])))] = page+1 if more else 1
                else: raise ValueError('HTTP '+str(status))
                state.update(failures=0,last_checked=start,next_due=start+source['interval'])
                if not source.get('urls') and not source['kind'].startswith('github_'):
                    state.update(etag=h.get('etag',state.get('etag') if status==304 else None),last_modified=h.get('last-modified',state.get('last_modified') if status==304 else None))
                else: state.pop('etag',None); state.pop('last_modified',None)
            except Exception as error:
                state['failures']=state.get('failures',0)+1
                backoff=min(21600,source['interval']*2**min(state['failures'],5))
                if 'h' in locals():
                    retry=h.get('retry-after','')
                    if retry.isdigit(): backoff=max(backoff,min(86400,int(retry)))
                    reset=h.get('x-ratelimit-reset','')
                    if reset.isdigit() and h.get('x-ratelimit-remaining')=='0': backoff=max(backoff,int(reset)-start)
                state.update(last_error=str(error)[:500],next_due=start+backoff)
                result.update(status='ERROR',error=str(error)[:500],next_due=utc(state['next_due']))
            con.execute('INSERT OR REPLACE INTO sources VALUES(?,?)',(source['id'],json.dumps(state)))
            receipt['sources'].append(result)
        receipt.update(end=utc(time.time() if now is None else now),result='PARTIAL' if any(s['status']=='ERROR' for s in receipt['sources']) else 'EVENTS_CACHED' if receipt['new_events'] else 'NO_CHANGE',actual_mode='non-LLM Python + system curl',cost='No AI call; network/host monetary cost UNKNOWN')
        con.execute('INSERT INTO receipts VALUES(?,?,?)',(receipt['id'],start,json.dumps(receipt))); con.commit()
    except BaseException:
        con.rollback(); raise
    finally: con.close()
    return receipt

def main():
    p=argparse.ArgumentParser(); p.add_argument('--manifest',type=Path,required=True); p.add_argument('--db',type=Path,required=True); p.add_argument('--trigger',choices=['manual-shadow','manual-production','launchd-interval'],required=True); a=p.parse_args()
    r=run(a.db,json.loads(a.manifest.read_text()),a.trigger); print(json.dumps(r,ensure_ascii=False),flush=True)
if __name__=='__main__': main()
