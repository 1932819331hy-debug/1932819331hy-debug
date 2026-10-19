#!/usr/bin/env python3
"""Read-only, non-LLM metadata probe. SQLite is DERIVED, never a task ledger.
No credentials, claim, dispatch, GitHub write or model invocation is implemented.
macOS system curl keeps OS TLS verification; Python installation's CA failed.
"""
import argparse,copy,datetime,email.utils,hashlib,json,os,re,sqlite3,subprocess,tempfile,time,uuid
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

def github_identity(url):
    """Use repository + number, not API object IDs that differ between endpoints."""
    parts=urlsplit(url)
    if parts.scheme!='https' or parts.hostname not in ('github.com','www.github.com'):
        return None,None,None,None
    path=[p for p in parts.path.split('/') if p]
    if len(path)<2: return None,None,None,None
    repo='/'.join(path[:2]).lower()
    if len(path)==4 and path[2] in ('issues','pull') and path[3].isdigit():
        kind='pull' if path[2]=='pull' else 'issue'; number=int(path[3])
        return 'github:'+repo+':'+kind+':'+str(number),repo,kind,number
    return None,repo,None,None

def normalize(source,data):
    """Validate a whole response before it can affect cached observations."""
    kind=source['kind']; items=[]; page=source.get('_page',1); limit=source.get('limit',100)
    if kind=='squash':
        if not isinstance(data,dict) or not isinstance(data.get('jobs'),list): raise ValueError('Missing jobs list')
        raw=data['jobs']; total=data.get('count',len(raw))
    elif kind=='github_search':
        if not isinstance(data,dict) or not isinstance(data.get('items'),list): raise ValueError('Missing items list')
        if data.get('incomplete_results'): raise ValueError('GitHub incomplete_results; retain prior coverage')
        raw=data['items']; total=data.get('total_count')
    elif kind=='github_pulls':
        if not isinstance(data,list): raise ValueError('Expected PR list')
        raw=data; total=None
    else: raise ValueError('Unsupported source parser')
    if total is not None and (type(total) is not int or total<0): raise ValueError('Invalid total count')
    if kind=='squash' and total!=len(raw): raise ValueError('Truncated jobs representation')
    if kind=='github_search':
        if total is None: raise ValueError('Missing total_count')
        expected=min(limit,max(0,min(total,1000)-(page-1)*limit))
        if len(raw)!=expected: raise ValueError('Truncated or inconsistent GitHub page')
    seen={}
    for x in raw:
        if not isinstance(x,dict): raise ValueError('Malformed item')
        if kind=='squash':
            identity=x.get('spec_id'); url=x.get('repo_url'); status=x.get('state')
            if not isinstance(identity,str) or not identity or not isinstance(url,str) or not url or not isinstance(status,str):
                raise ValueError('Missing Squash identity/repo_url/state')
            amount=x.get('amount_minor')
            if amount is not None and type(amount) is not int: raise ValueError('Invalid amount_minor')
            task_id,repo,task_kind,number=github_identity(url)
            task_id=task_id or 'squash:'+identity
            meaningful={k:x.get(k) for k in ('amount_minor','currency','state','internal')}
            meaningful['source_present']=True
            snapshot=dict(meaningful,task_id=task_id,repo=repo,task_kind=task_kind,number=number,
                          pr=number if task_kind=='pull' else None,head=None,squash_id=identity,
                          repo_url=url,source_event_ids=['squash:'+identity],created_at=x.get('created_ts'),updated_at=x.get('updated_ts'))
        else:
            user=x.get('user'); identity=x.get('id'); url=x.get('html_url'); status=x.get('state')
            if not isinstance(user,dict) or not isinstance(user.get('login'),str) or not isinstance(identity,(int,str)) or isinstance(identity,bool) or not isinstance(url,str) or not isinstance(status,str):
                raise ValueError('Missing GitHub identity/user/url/state')
            task_id,repo,task_kind,number=github_identity(url)
            if not task_id: raise ValueError('Unrecognized GitHub task URL')
            if source.get('account') and user['login'].casefold()!=source['account'].casefold(): continue
            meaningful={'state':status,'draft':x.get('draft'),'merged':x.get('merged'),'merged_at':x.get('merged_at')}
            if kind=='github_pulls':
                if not isinstance(x.get('head'),dict) or not isinstance(x['head'].get('sha'),str): raise ValueError('Missing PR head')
                if type(x.get('draft')) is not bool: raise ValueError('Missing PR draft flag')
                meaningful['head']=x['head']['sha']
            labels=x.get('labels',[])
            if not isinstance(labels,list): raise ValueError('Malformed labels')
            names=[label.get('name','') if isinstance(label,dict) else label for label in labels]
            if any(not isinstance(name,str) for name in names): raise ValueError('Malformed label name')
            meaningful['funding_labels']=sorted({name.casefold() for name in names if re.search(r'bounty|funded|reward|paid|payment|claim',name,re.I)})
            snapshot=dict(meaningful,task_id=task_id,repo=repo,task_kind=task_kind,number=number,
                          pr=number if task_kind=='pull' else None,head=meaningful.get('head'),squash_id=None,
                          amount_minor=None,currency=None,html_url=url,author=user['login'],source_event_ids=['github:'+str(identity)],
                          title=x.get('title'),comments=x.get('comments'),created_at=x.get('created_at'),updated_at=x.get('updated_at'))
        fingerprint=digest(meaningful)
        if task_id in seen:
            if seen[task_id]!=fingerprint: raise ValueError('Conflicting duplicate task in response')
            continue
        seen[task_id]=fingerprint
        items.append((task_id,snapshot,meaningful))
    more=(kind=='github_pulls' and len(raw)>=limit) or (kind=='github_search' and page*limit<min(total,1000))
    coverage={'returned':len(raw),'total':total,'saturated':len(raw)>=limit or (total is not None and total>len(raw)),
              'has_more':more,'complete':not more and not (kind=='github_search' and total>1000),
              'api_result_cap':kind=='github_search' and total>1000,
              'watermark':max((str(item[1]['updated_at']) for item in items if item[1].get('updated_at') is not None),default=None)}
    return items,coverage

def migrate(con):
    con.executescript('''CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items(source TEXT,id TEXT,fingerprint TEXT,PRIMARY KEY(source,id));
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,source TEXT,item TEXT,payload TEXT,observed REAL);
CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,created REAL,payload TEXT);
CREATE TABLE IF NOT EXISTS observations(source TEXT,representation TEXT,item TEXT,fingerprint TEXT,data TEXT,
PRIMARY KEY(source,representation,item));''')
    columns={row[1] for row in con.execute('PRAGMA table_info(items)')}
    for name,kind in [('data','TEXT'),('revision','INTEGER'),('spec_digest','TEXT'),('transition','TEXT')]:
        if name not in columns: con.execute('ALTER TABLE items ADD COLUMN '+name+' '+kind)

def quarantine_legacy(con):
    count=0
    for event_id,raw in con.execute('SELECT id,payload FROM events').fetchall():
        try: payload=json.loads(raw)
        except (TypeError,ValueError): payload={'legacy_payload':raw}
        if not isinstance(payload,dict): payload={'legacy_payload':payload}
        if payload.get('schema_version')==2 or payload.get('kind')=='LEGACY_UNVERIFIED': continue
        payload.update(schema_version=1,legacy_kind=payload.get('kind'),kind='LEGACY_UNVERIFIED',status='LEGACY_UNVERIFIED',
                       processing_status='LEGACY_UNVERIFIED',dispatch_allowed=False,publish_allowed=False,model_calls=0)
        con.execute('UPDATE events SET payload=? WHERE id=?',(json.dumps(payload),event_id)); count+=1
    return count

def retry_delay(headers,start,interval,failures):
    delay=min(21600,interval*2**min(failures,5))
    retry=headers.get('retry-after','')
    if retry.isdigit(): delay=max(delay,min(86400,int(retry)))
    elif retry:
        try: delay=max(delay,min(86400,email.utils.parsedate_to_datetime(retry).timestamp()-start))
        except (TypeError,ValueError,OverflowError): pass
    reset=headers.get('x-ratelimit-reset','')
    if reset.isdigit() and headers.get('x-ratelimit-remaining')=='0': delay=max(delay,min(86400,int(reset)-start))
    return delay

def fresh_candidate(source,snapshot,cached,start):
    """A verified creation time can distinguish a new funded listing from an old first sight."""
    if not cached or 'last_success' not in cached: return False
    stamp=snapshot.get('created_at')
    try:
        if type(stamp) in (int,float): created=float(stamp)
        elif isinstance(stamp,str):
            parsed=datetime.datetime.fromisoformat(stamp.replace('Z','+00:00'))
            if parsed.tzinfo is None: return False
            created=parsed.timestamp()
        else: return False
        if not cached['last_success']<created<=start: return False
    except (ValueError,TypeError,OverflowError): return False
    if source['kind']=='squash':
        return (snapshot.get('state')=='funded' and type(snapshot.get('amount_minor')) is int
                and snapshot['amount_minor']>0 and isinstance(snapshot.get('currency'),str)
                and bool(snapshot['currency'].strip())
                and (snapshot.get('internal') is False or snapshot.get('internal') is None))
    return snapshot.get('state')=='open' and bool(snapshot.get('funding_labels'))

def run(db,manifest,trigger,now=None,transport=fetch):
    start=time.time() if now is None else now
    Path(db).parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(db,timeout=1)
    migrate(con)
    # Transaction serializes this local derived DB only, never cross-host ownership.
    con.execute('BEGIN IMMEDIATE')
    prior=con.execute('SELECT id FROM receipts ORDER BY created DESC,rowid DESC LIMIT 1').fetchone()
    receipt={'id':'probe-'+uuid.uuid4().hex,'trigger':trigger,'pid':os.getpid(),'parent_pid':os.getppid(),'program_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'manifest_sha256':digest(manifest),'start':utc(start),'prior_receipt_id':prior[0] if prior else None,'canonical_page':manifest['canonical_page'],'cache_role':'DERIVED_NO_DISPATCH_AUTHORITY','sources':[],'new_events':0,'model_calls':0,'public_writes':0,'executor_delivery':'NOT_CONFIGURED','host':'runtime process; no ownership claim'}
    try:
        receipt['legacy_events_quarantined']=quarantine_legacy(con)
        receipt['source_budget']={'limit':4,'configured':len(manifest['sources']),'processed':min(4,len(manifest['sources'])),
                                  'deferred_source_ids':[source['id'] for source in manifest['sources'][4:]],
                                  'coverage':'PARTIAL_BUDGET' if len(manifest['sources'])>4 else 'WITHIN_BUDGET'}
        for source in manifest['sources'][:4]:
            row=con.execute('SELECT state FROM sources WHERE id=?',(source['id'],)).fetchone(); state=json.loads(row[0]) if row else {}
            spec=digest({key:source.get(key) for key in ('kind','url','urls','account','limit')})
            if state.get('schema_version')!=2 or state.get('spec_digest')!=spec:
                state={'schema_version':2,'spec_digest':spec,'partition':0,'pages':{},'representations':{}}
            result={'source':source['id'],'query_spec_digest':spec,'coverage':copy.deepcopy(state.get('coverage',{})),
                    'cursor':{'rotation':state.get('partition',0),'pages':copy.deepcopy(state.get('pages',{}))},
                    'last_success_at':utc(state['last_success']) if 'last_success' in state else None}
            if start<state.get('next_due',0):
                result.update(status='NOT_DUE',next_due=utc(state['next_due'])); receipt['sources'].append(result); continue
            partition=state.get('partition',0); chosen=dict(source)
            urls=source.get('urls') or [source['url']]; partition_index=partition%len(urls); chosen['url']=urls[partition_index]
            page=state.get('pages',{}).get(str(partition_index),1)
            if source['kind'].startswith('github_'):
                parts=urlsplit(chosen['url']); query=dict(parse_qsl(parts.query)); query['page']=str(page)
                chosen['url']=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),parts.fragment))
            representation_key=digest([spec,partition_index,page,chosen['url']])
            cached=state.get('representations',{}).get(representation_key)
            headers={}
            if cached and cached.get('etag'): headers['If-None-Match']=cached['etag']
            if cached and cached.get('last_modified'): headers['If-Modified-Since']=cached['last_modified']
            h={}; successful_state=copy.deepcopy(state); inserted=0; event_counts={}
            result.update(url=chosen['url'],page=page,partition=partition_index,query_spec_digest=spec,checked_at=utc(start))
            con.execute('SAVEPOINT source_response')
            try:
                status,h,body=transport(chosen,headers); result.update(http_status=status,url=chosen['url'])
                h={key.lower():str(value) for key,value in h.items()}
                if status==304:
                    if not cached or 'last_success' not in cached: raise ValueError('304 without matching cached successful representation')
                    coverage=cached['coverage']; result.update(status='NOT_MODIFIED',**coverage)
                    cached=dict(cached,etag=h.get('etag',cached.get('etag')),last_modified=h.get('last-modified',cached.get('last_modified')))
                elif status==200:
                    if h.get('content-length','').isdigit() and int(h['content-length'])!=len(body): raise ValueError('Truncated HTTP representation')
                    items,coverage=normalize(dict(source,_page=page),json.loads(body)); result.update(status='OK',**coverage)
                    baseline=cached is None
                    if source['kind']=='squash' and not baseline and coverage['complete']:
                        present_ids={identity for identity,_,_ in items}
                        previous=con.execute('SELECT item,data FROM observations WHERE source=? AND representation=?',
                                             (source['id'],representation_key)).fetchall()
                        for identity,raw in previous:
                            snapshot=json.loads(raw)
                            if identity in present_ids or snapshot.get('source_present') is False: continue
                            snapshot['source_present']=False
                            meaningful={key:snapshot.get(key) for key in ('amount_minor','currency','state','internal','source_present')}
                            items.append((identity,snapshot,meaningful))
                    for identity,snapshot,meaningful in items:
                        fingerprint=digest(meaningful)
                        old=con.execute('SELECT fingerprint,data,revision,spec_digest,transition FROM items WHERE source=? AND id=?',(source['id'],identity)).fetchone()
                        observation=con.execute('SELECT fingerprint,data FROM observations WHERE source=? AND representation=? AND item=?',
                                                (source['id'],representation_key,identity)).fetchone()
                        before=json.loads(observation[1]) if observation else None
                        global_same=old and old[0]==fingerprint and old[3]==spec
                        locally_same=observation and observation[0]==fingerprint
                        transition=digest([observation[0],fingerprint]) if observation else None
                        duplicate_transition=global_same and old[4]==transition
                        emit=(bool(observation) and not locally_same and not duplicate_transition) or (not observation and not global_same)
                        kind=None
                        if emit:
                            revision=(old[2] or 0)+1 if old else 1
                            newly_created=before is None and fresh_candidate(source,snapshot,cached,start)
                            kind='BASELINE_UNTRIAGED' if baseline or (old and old[3]!=spec) else 'FIRST_OBSERVED_UNVERIFIED' if before is None and not newly_created else 'VALIDATION_REQUIRED'
                            event_id=digest([2,source['id'],identity,revision,old[0] if old else None,fingerprint])
                            diff={key:{'before':before.get(key) if before else None,'after':value} for key,value in meaningful.items() if before is None or before.get(key)!=value}
                            payload={'schema_version':2,'source':source['id'],'source_id':source['id'],'canonical_id':identity,'task_id':identity,
                                     'data':snapshot,'before':before,'after':snapshot,'diff':diff,'kind':kind,'status':kind,'processing_status':kind,
                                     'repo':snapshot.get('repo'),'pr':snapshot.get('pr'),'head':snapshot.get('head'),
                                     'amount_minor':snapshot.get('amount_minor'),'currency':snapshot.get('currency'),'task_status':snapshot.get('state'),
                                     'squash_id':snapshot.get('squash_id'),'source_event_ids':snapshot['source_event_ids'],
                                     'evidence':{'representation_url':chosen['url'],'query_spec_digest':spec,'partition':partition_index,'page':page,
                                                 'response_sha256':hashlib.sha256(body).hexdigest(),'observed_at':utc(start),'prior_observation_available':before is not None,
                                                 'fresh_creation_after_baseline':newly_created,
                                                 'presence_reason':'ABSENT_FROM_COMPLETE_SOURCE_LISTING_UNVERIFIED' if snapshot.get('source_present') is False else 'PRESENT_IN_SUCCESSFUL_SOURCE_RESPONSE'},
                                     'revision':revision,'builder':'UNKNOWN','publisher':'UNKNOWN','dispatch_allowed':False,'publish_allowed':False,'model_calls':0,
                                     'authority':'CANONICAL_PAGE_ONLY','cash_opportunity_verified':False}
                            added=con.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)',(event_id,source['id'],identity,json.dumps(payload),start)).rowcount
                            inserted+=added;event_counts[kind]=event_counts.get(kind,0)+added
                        else: revision=(old[2] or 0) if old else 0
                        # An unchanged overlapping page must not revert the latest canonical state.
                        update_global=not old or old[3]!=spec or global_same or kind=='VALIDATION_REQUIRED'
                        last_transition=transition if kind=='VALIDATION_REQUIRED' else old[4] if old and old[3]==spec else None
                        con.execute('INSERT OR REPLACE INTO items(source,id,fingerprint,data,revision,spec_digest,transition) VALUES(?,?,?,?,?,?,?)',
                                    (source['id'],identity,fingerprint if update_global else old[0],json.dumps(snapshot) if update_global else old[1],revision,spec,last_transition))
                        con.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?)',
                                    (source['id'],representation_key,identity,fingerprint,json.dumps(snapshot)))
                    cached={'last_success':start,'url':chosen['url'],'partition':partition_index,'page':page,'coverage':coverage,
                            'etag':h.get('etag'),'last_modified':h.get('last-modified')}
                else: raise ValueError('HTTP '+str(status))
                state.setdefault('representations',{})[representation_key]=dict(cached,last_checked=start,checked_at=utc(start),last_success_at=utc(cached['last_success']))
                state.update(last_success=start,coverage=dict(coverage,checked_at=utc(start),partition=partition_index,page=page,
                                                              scope='EXACT_SOURCE_QUERY_PARTITION_PAGE'),saturated=coverage['saturated'])
                state['partition']=partition+1
                state.setdefault('pages',{})[str(partition_index)] = page+1 if coverage['has_more'] else 1
                state.update(failures=0,last_checked=start,next_due=start+source['interval'])
                result.update(coverage=copy.deepcopy(state['coverage']),last_success_at=utc(start),
                              cursor={'rotation':state['partition'],'pages':copy.deepcopy(state['pages'])})
                state.pop('last_error',None)
                con.execute('RELEASE source_response');receipt['new_events']+=inserted
                for kind,count in event_counts.items(): receipt.setdefault('event_counts',{})[kind]=receipt.get('event_counts',{}).get(kind,0)+count
            except Exception as error:
                con.execute('ROLLBACK TO source_response');con.execute('RELEASE source_response');state=successful_state
                state['failures']=state.get('failures',0)+1
                backoff=retry_delay(h,start,source['interval'],state['failures'])
                state.update(last_error=str(error)[:500],next_due=start+backoff)
                result.update(status='ERROR',error=str(error)[:500],next_due=utc(state['next_due']))
            con.execute('INSERT OR REPLACE INTO sources VALUES(?,?)',(source['id'],json.dumps(state)))
            receipt['sources'].append(result)
        receipt.setdefault('event_counts',{})
        receipt.update(baseline_events=receipt['event_counts'].get('BASELINE_UNTRIAGED',0),
                       first_observed_unverified_events=receipt['event_counts'].get('FIRST_OBSERVED_UNVERIFIED',0),
                       meaningful_events=receipt['event_counts'].get('VALIDATION_REQUIRED',0))
        receipt.update(end=utc(time.time() if now is None else now),result='PARTIAL' if any(s['status']=='ERROR' for s in receipt['sources']) or receipt['source_budget']['deferred_source_ids'] else 'EVENTS_CACHED' if receipt['new_events'] else 'NO_CHANGE',actual_mode='non-LLM Python + system curl',cost='No AI call; network/host monetary cost UNKNOWN')
        con.execute('INSERT INTO receipts VALUES(?,?,?)',(receipt['id'],start,json.dumps(receipt))); con.commit()
    except BaseException:
        con.rollback(); raise
    finally: con.close()
    return receipt

def main():
    p=argparse.ArgumentParser(); p.add_argument('--manifest',type=Path,required=True); p.add_argument('--db',type=Path,required=True); p.add_argument('--trigger',choices=['manual-shadow','manual-production','launchd-interval'],required=True); a=p.parse_args()
    r=run(a.db,json.loads(a.manifest.read_text()),a.trigger); print(json.dumps(r,ensure_ascii=False),flush=True)
if __name__=='__main__': main()
