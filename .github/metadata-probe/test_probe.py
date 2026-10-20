"""Boundary regressions: real SQLite persistence, isolated HTTP responses only."""
import copy,importlib.util,json,sqlite3,tempfile,unittest
from pathlib import Path
SPEC=importlib.util.spec_from_file_location('probe',Path(__file__).with_name('bounty_probe.py'))
class ProbeTests(unittest.TestCase):
 def test_new_funded_task_created_after_baseline_requires_validation(self):
  self.run_probe(self.response([self.job()]),now=1000)
  fresh=dict(self.job(),spec_id='gh_1_3',repo_url='https://github.com/x/y/issues/3',created_ts=1200)
  r=self.run_probe(self.response([self.job(),fresh]),now=1601)
  self.assertEqual(r['meaningful_events'],1)
  self.assertEqual(r['first_observed_unverified_events'],0)
  with self.p.sqlite3.connect(self.db) as c:
   e=json.loads(c.execute('SELECT payload FROM events ORDER BY observed DESC LIMIT 1').fetchone()[0])
  self.assertTrue(e['evidence']['fresh_creation_after_baseline'])
  self.assertFalse(e['dispatch_allowed'])
  self.assertFalse(e['cash_opportunity_verified'])
 def test_future_created_task_does_not_consume_validation_budget(self):
  self.run_probe(self.response([self.job()]),now=1000)
  future=dict(self.job(),spec_id='gh_1_3',repo_url='https://github.com/x/y/issues/3',created_ts=99999)
  r=self.run_probe(self.response([self.job(),future]),now=1601)
  self.assertEqual(r['meaningful_events'],0)
  self.assertEqual(r['first_observed_unverified_events'],1)
 def test_malformed_funding_metadata_does_not_consume_validation_budget(self):
  self.run_probe(self.response([self.job()]),now=1000)
  fresh=dict(self.job(),spec_id='gh_1_3',repo_url='https://github.com/x/y/issues/3',created_ts=1200,internal=1)
  r=self.run_probe(self.response([self.job(),fresh]),now=1601)
  self.assertEqual(r['meaningful_events'],0)
  other=dict(self.job(),spec_id='gh_1_4',repo_url='https://github.com/x/y/issues/4',created_ts=1800,currency={'code':'USD'})
  r=self.run_probe(self.response([self.job(),fresh,other]),now=2202)
  self.assertEqual(r['meaningful_events'],0)
 def setUp(self):
  self.assertTrue(Path(SPEC.origin).exists(),'probe implementation has not been created')
  self.p=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(self.p)
  self.t=tempfile.TemporaryDirectory(); self.db=Path(self.t.name)/'cache.db'
  self.source={'id':'fast','kind':'squash','url':'https://app.squash.ci/jobs.json','interval':600,'limit':100}
 def tearDown(self):
  if hasattr(self,'t'): self.t.cleanup()
 def response(self,jobs): return (200,{'etag':'"v1"'},json.dumps({'count':len(jobs),'jobs':jobs}).encode())
 def job(self): return {'spec_id':'gh_1_2','repo_url':'https://github.com/x/y/issues/1','amount_minor':500,'currency':'USD','state':'funded','created_ts':1}
 def run_probe(self,response,now=1000,sources=None):
  return self.p.run(self.db,{'canonical_page':'page_existing','sources':sources or [self.source]},'fixture',now,lambda s,h:response)
 def events(self):
  with sqlite3.connect(self.db) as con:
   return [(row[0],json.loads(row[1])) for row in con.execute('SELECT id,payload FROM events ORDER BY observed,rowid')]
 def state(self,source='fast'):
  with sqlite3.connect(self.db) as con:
   return json.loads(con.execute('SELECT state FROM sources WHERE id=?',(source,)).fetchone()[0])
 def gh_item(self,**changes):
  item={'id':10,'number':1,'html_url':'https://github.com/a/b/issues/1','user':{'login':'a'},'state':'open','comments':0,'title':'cash','updated_at':'old'}
  item.update(changes);return item
 def gh_response(self,items,total=None,**extra):
  return 200,{},json.dumps(dict(total_count=len(items) if total is None else total,items=items,**extra)).encode()
 def test_duplicate_restart_and_receipt_chain(self):
  a=self.run_probe(self.response([self.job()])); b=self.run_probe(self.response([self.job()]),1601)
  self.assertEqual((a['new_events'],b['new_events']),(1,0)); self.assertEqual(b['prior_receipt_id'],a['id'])
  self.assertEqual(b['model_calls'],0); self.assertEqual(b['public_writes'],0)
 def test_error_preserves_cursor_and_is_not_empty(self):
  a=self.run_probe(self.response([self.job()])); b=self.run_probe((503,{},b'bad'),1601)
  self.assertEqual(b['sources'][0]['status'],'ERROR'); self.assertEqual(b['new_events'],0)
  c=self.run_probe(self.response([self.job()]),10000); self.assertEqual(c['new_events'],0)
 def test_malformed_not_empty_and_other_source_continues(self):
  sources=[self.source,dict(self.source,id='healthy')]
  r=self.p.run(self.db,{'canonical_page':'page_existing','sources':sources},'fixture',1000,lambda s,h:(200,{},b'{"unexpected":[]}') if s['id']=='fast' else self.response([self.job()]))
  self.assertEqual([s['status'] for s in r['sources']],['ERROR','OK']); self.assertEqual(r['new_events'],1)
 def test_self_timestamp_does_not_create_event_and_foreign_pr_excluded(self):
  s=dict(self.source,kind='github_search',id='own',account='1932819331hy-debug',limit=30)
  item={'id':10,'number':1,'html_url':'https://github.com/a/b/pull/1','user':{'login':'1932819331hy-debug'},'state':'open','comments':0,'updated_at':'old','pull_request':{}}
  a=self.run_probe((200,{},json.dumps({'total_count':1,'items':[item]}).encode()),sources=[s]); item['updated_at']='self-change'
  foreign=dict(item,id=11,user={'login':'sidtrey'})
  b=self.run_probe((200,{},json.dumps({'total_count':2,'items':[item,foreign]}).encode()),1601,[s])
  self.assertEqual((a['new_events'],b['new_events']),(1,0))
 def test_saturation_and_not_modified(self):
  s=dict(self.source,limit=1)
  a=self.run_probe(self.response([self.job()]),sources=[s]); self.assertTrue(a['sources'][0]['saturated'])
  b=self.run_probe((304,{},b''),1601,[s]); self.assertEqual(b['sources'][0]['status'],'NOT_MODIFIED'); self.assertEqual(b['new_events'],0)
 def test_capped_query_advances_page_after_restart(self):
  s=dict(self.source,kind='github_search',limit=1)
  item={'id':10,'html_url':'https://github.com/a/b/issues/1','user':{'login':'a'},'state':'open','comments':0,'title':'cash'}
  r=self.run_probe((200,{},json.dumps({'total_count':2,'items':[item]}).encode()),sources=[s])
  seen=[]
  def transport(source,headers):
   seen.append(source['url']); return (200,{},json.dumps({'total_count':2,'items':[]}).encode())
  self.p.run(self.db,{'canonical_page':'page_existing','sources':[s]},'fixture',1601,transport)
  self.assertIn('page=2',seen[0])
 def test_conditional_validator_survives_304_without_headers(self):
  self.run_probe(self.response([self.job()]))
  self.run_probe((304,{},b''),1601)
  seen=[]
  def transport(source,headers):
   seen.append(headers);return (304,{},b'')
  self.p.run(self.db,{'canonical_page':'page_existing','sources':[self.source]},'fixture',2202,transport)
  self.assertEqual(seen[0].get('If-None-Match'),'"v1"')
 def test_304_without_cache_is_error(self):
  r=self.run_probe((304,{},b'')); self.assertEqual(r['sources'][0]['status'],'ERROR')
 def test_first_page_of_each_partition_has_its_own_baseline(self):
  s=dict(self.source,kind='github_search',urls=['https://api.github.com/search/issues?q=first','https://api.github.com/search/issues?q=second'])
  self.run_probe(self.gh_response([self.gh_item()]),sources=[s])
  self.run_probe(self.gh_response([self.gh_item(id=11,number=2,html_url='https://github.com/a/b/issues/2')]),1601,[s])
  self.assertEqual([p['kind'] for _,p in self.events()],['BASELINE_UNTRIAGED','BASELINE_UNTRIAGED'])
 def test_first_visit_to_later_page_is_baseline(self):
  s=dict(self.source,kind='github_search',limit=1)
  self.run_probe(self.gh_response([self.gh_item()],2),sources=[s])
  self.run_probe(self.gh_response([self.gh_item(id=11,number=2,html_url='https://github.com/a/b/issues/2')],2),1601,[s])
  self.assertEqual(self.events()[-1][1]['kind'],'BASELINE_UNTRIAGED')
 def test_old_unseen_item_on_covered_page_is_unverified(self):
  self.run_probe(self.response([self.job()]))
  old=dict(self.job(),spec_id='other',repo_url='https://github.com/x/y/issues/2',created_ts=0)
  self.run_probe(self.response([self.job(),old]),1601)
  event=self.events()[-1][1]
  self.assertEqual(event['kind'],'FIRST_OBSERVED_UNVERIFIED')
  self.assertFalse(event['dispatch_allowed']); self.assertEqual(event['model_calls'],0)
 def test_query_change_resets_baseline_even_for_changed_known_item(self):
  self.run_probe(self.response([self.job()]))
  source=dict(self.source,url='https://app.squash.ci/jobs.json?scope=changed')
  self.run_probe(self.response([dict(self.job(),amount_minor=600)]),1601,[source])
  self.assertEqual(self.events()[-1][1]['kind'],'BASELINE_UNTRIAGED')
 def test_title_comments_and_updated_at_are_not_meaningful(self):
  s=dict(self.source,kind='github_search')
  self.run_probe(self.gh_response([self.gh_item()]),sources=[s])
  r=self.run_probe(self.gh_response([self.gh_item(title='edited',comments=3,updated_at='new')]),1601,[s])
  self.assertEqual(r['new_events'],0)
 def test_return_to_prior_state_is_real_transition_and_retry_is_deduped(self):
  self.run_probe(self.response([self.job()]))
  change=dict(self.job(),amount_minor=600)
  a=self.run_probe(self.response([change]),1601)
  b=self.run_probe(self.response([self.job()]),2202)
  c=self.run_probe(self.response([self.job()]),2803)
  self.assertEqual((a['new_events'],b['new_events'],c['new_events']),(1,1,0))
  events=self.events(); self.assertEqual(len({i for i,_ in events}),3)
  event=events[-1][1]
  self.assertEqual(event['kind'],'VALIDATION_REQUIRED');self.assertEqual(event['before']['amount_minor'],600)
  self.assertEqual(event['after']['amount_minor'],500); self.assertEqual(event['diff']['amount_minor'],{'before':600,'after':500})
  self.assertTrue(event['evidence']['representation_url']);self.assertFalse(event['dispatch_allowed'])
 def test_canonical_task_identity_matches_github_and_squash(self):
  self.run_probe(self.response([self.job()]))
  s=dict(self.source,id='github',kind='github_search')
  self.run_probe(self.gh_response([self.gh_item(html_url='https://github.com/X/Y/issues/1/?query=ignore')]),1601,[s])
  payloads=[p for _,p in self.events()]
  self.assertEqual(payloads[0]['task_id'],payloads[1]['task_id'])
  self.assertEqual(payloads[0]['squash_id'],'gh_1_2');self.assertEqual(payloads[1]['repo'],'x/y')
  self.assertEqual(payloads[0]['source_id'],'fast');self.assertEqual(payloads[1]['source_event_ids'],['github:10'])
 def test_legacy_events_are_quarantined_without_replay(self):
  with sqlite3.connect(self.db) as con:
   con.executescript('CREATE TABLE events(id TEXT PRIMARY KEY,source TEXT,item TEXT,payload TEXT,observed REAL);')
   con.execute('INSERT INTO events VALUES(?,?,?,?,?)',('legacy','fast','old',json.dumps({'kind':'METADATA_CHANGE_REQUIRES_HUMAN_VERIFICATION','dispatch_allowed':True}),1))
  r=self.run_probe(self.response([]))
  self.assertEqual(r['new_events'],0)
  p=self.events()[0][1];self.assertEqual(p['kind'],'LEGACY_UNVERIFIED');self.assertFalse(p['dispatch_allowed'])
 def test_304_requires_exact_partition_and_page_cache(self):
  s=dict(self.source,kind='github_search',urls=['https://api.github.com/search/issues?q=one','https://api.github.com/search/issues?q=two'])
  self.run_probe(self.gh_response([self.gh_item()]),sources=[s])
  before=self.state()
  r=self.run_probe((304,{},b''),1601,[s])
  self.assertEqual(r['sources'][0]['status'],'ERROR');self.assertEqual(self.state()['partition'],before['partition'])
 def test_success_at_epoch_zero_can_be_revalidated(self):
  self.run_probe(self.response([self.job()]),0)
  r=self.run_probe((304,{},b''),601)
  self.assertEqual(r['sources'][0]['status'],'NOT_MODIFIED')
 def test_failed_response_never_partially_advances_items_or_cursor(self):
  self.run_probe(self.response([self.job()]))
  before=copy.deepcopy(self.state())
  broken=dict(self.job(),spec_id='bad');del broken['repo_url']
  r=self.run_probe(self.response([dict(self.job(),amount_minor=600),broken]),1601)
  self.assertEqual(r['sources'][0]['status'],'ERROR');self.assertEqual(r['new_events'],0)
  self.assertEqual(self.state()['partition'],before['partition'])
  r=self.run_probe(self.response([self.job()]),10000);self.assertEqual(r['new_events'],0)
 def test_valid_json_truncated_page_is_error_and_retains_cursor(self):
  s=dict(self.source,kind='github_search',limit=2)
  r=self.run_probe(self.gh_response([self.gh_item()],4),sources=[s])
  self.assertEqual(r['sources'][0]['status'],'ERROR');self.assertEqual(r['new_events'],0)
 def test_rate_limit_and_transport_failure_preserve_cursor(self):
  self.run_probe(self.response([self.job()]))
  before=self.state()['partition']
  r=self.run_probe((429,{'retry-after':'4000'},b''),1601)
  self.assertEqual(r['sources'][0]['status'],'ERROR');self.assertGreaterEqual(self.state()['next_due'],5601)
  self.assertEqual(self.state()['partition'],before)
  def timeout(source,headers): raise TimeoutError('fixture timeout')
  r=self.p.run(self.db,{'canonical_page':'page_existing','sources':[self.source]},'fixture',10000,timeout)
  self.assertEqual(r['sources'][0]['status'],'ERROR');self.assertEqual(r['new_events'],0)
  self.assertEqual(self.state()['partition'],before)
 def test_unchanged_overlapping_partitions_do_not_manufacture_transitions(self):
  s=dict(self.source,urls=['https://app.squash.ci/jobs.json?partition=a','https://app.squash.ci/jobs.json?partition=b'])
  self.run_probe(self.response([self.job()]),sources=[s])
  self.run_probe(self.response([dict(self.job(),amount_minor=600)]),1601,[s])
  a=self.run_probe(self.response([self.job()]),2202,[s])
  b=self.run_probe(self.response([dict(self.job(),amount_minor=600)]),2803,[s])
  self.assertEqual((a['new_events'],b['new_events']),(0,0))
 def test_receipt_separates_baseline_and_meaningful_event_counts(self):
  a=self.run_probe(self.response([self.job()]))
  b=self.run_probe(self.response([dict(self.job(),amount_minor=600)]),1601)
  self.assertEqual(a.get('baseline_events'),1);self.assertEqual(a.get('meaningful_events'),0)
  self.assertEqual(b.get('baseline_events'),0);self.assertEqual(b.get('meaningful_events'),1)
 def test_304_advances_rotation_with_cached_coverage(self):
  s=dict(self.source,urls=['https://app.squash.ci/jobs.json?a','https://app.squash.ci/jobs.json?b'])
  self.run_probe(self.response([self.job()]),sources=[s])
  self.run_probe(self.response([self.job()]),1601,[s])
  r=self.run_probe((304,{},b''),2202,[s]);self.assertEqual(r['sources'][0]['status'],'NOT_MODIFIED')
  seen=[]
  def transport(source,headers):
   seen.append(source['url']);return self.response([self.job()])
  self.p.run(self.db,{'canonical_page':'page_existing','sources':[s]},'fixture',2803,transport)
  self.assertTrue(seen[0].endswith('?b'))
 def test_pr_head_and_status_transitions_have_explicit_evidence(self):
  s=dict(self.source,kind='github_pulls',account='a')
  item=self.gh_item(html_url='https://github.com/a/b/pull/1',head={'sha':'a'},draft=False)
  self.run_probe((200,{},json.dumps([item]).encode()),sources=[s])
  changed=dict(item,head={'sha':'b'},state='closed')
  r=self.run_probe((200,{},json.dumps([changed]).encode()),1601,[s])
  self.assertEqual(r['meaningful_events'],1)
  event=self.events()[-1][1]
  self.assertEqual(event['task_id'],'github:a/b:pull:1');self.assertEqual(event['pr'],1);self.assertEqual(event['head'],'b')
  self.assertEqual(event['diff']['head'],{'before':'a','after':'b'})
  self.assertEqual(event['diff']['state'],{'before':'open','after':'closed'})
  self.assertEqual(event['processing_status'],'VALIDATION_REQUIRED');self.assertEqual(event['schema_version'],2)
  self.assertFalse(event['dispatch_allowed']);self.assertFalse(event['publish_allowed']);self.assertEqual(event['model_calls'],0)
 def test_all_160_historical_events_quarantine_without_reinsertion(self):
  with sqlite3.connect(self.db) as con:
   con.execute('CREATE TABLE events(id TEXT PRIMARY KEY,source TEXT,item TEXT,payload TEXT,observed REAL)')
   con.executemany('INSERT INTO events VALUES(?,?,?,?,?)',[(str(i),'fast',str(i),json.dumps({'kind':'BASELINE_UNTRIAGED'}),1) for i in range(160)])
  a=self.run_probe(self.response([]));b=self.run_probe(self.response([]),1601)
  self.assertEqual(a['legacy_events_quarantined'],160);self.assertEqual(b['legacy_events_quarantined'],0)
  self.assertEqual(len(self.events()),160);self.assertEqual(b['new_events'],0)
  self.assertTrue(all(p['processing_status']=='LEGACY_UNVERIFIED' and not p['dispatch_allowed'] for _,p in self.events()))
 def test_failure_classes_preserve_successful_query_coverage(self):
  s=dict(self.source,kind='github_search')
  self.run_probe(self.gh_response([self.gh_item()]),sources=[s]); before=self.state()
  failures=[(403,{'x-ratelimit-remaining':'0','x-ratelimit-reset':'20000'},b''),
            (200,{},b'{truncated'),self.gh_response([self.gh_item()],incomplete_results=True),
            (200,{'content-length':'10000'},self.gh_response([self.gh_item()])[2])]
  for n,response in enumerate(failures):
   with self.subTest(response=response):
    r=self.run_probe(response,30000*(n+1),[s]);self.assertEqual(r['sources'][0]['status'],'ERROR')
    self.assertEqual(r['new_events'],0);self.assertEqual(self.state()['pages'],before['pages'])
    self.assertEqual(self.state()['partition'],before['partition']);self.assertEqual(self.state()['representations'],before['representations'])
 def test_search_result_cap_remains_incomplete(self):
  s=dict(self.source,kind='github_search',limit=1)
  r=self.run_probe(self.gh_response([self.gh_item()],1001),sources=[s])
  self.assertTrue(r['sources'][0]['api_result_cap']);self.assertFalse(r['sources'][0]['complete'])
 def test_no_more_than_four_sources_are_read(self):
  seen=[]
  def transport(source,headers):seen.append(source['id']);return self.response([])
  r=self.p.run(self.db,{'canonical_page':'page_existing','sources':[dict(self.source,id=str(i)) for i in range(5)]},'fixture',1000,transport)
  self.assertEqual(seen,['0','1','2','3']);self.assertEqual(r['model_calls'],0);self.assertEqual(r['public_writes'],0)
  self.assertEqual(r.get('source_budget',{}).get('deferred_source_ids'),['4'])
  self.assertEqual(r['source_budget']['coverage'],'PARTIAL_BUDGET');self.assertEqual(r['result'],'PARTIAL')
 def test_complete_squash_listing_absence_and_return_are_unverified_transitions(self):
  self.run_probe(self.response([self.job()]))
  absent=self.run_probe(self.response([]),1601)
  repeated=self.run_probe(self.response([]),2202)
  returned=self.run_probe(self.response([self.job()]),2803)
  self.assertEqual((absent['meaningful_events'],repeated['new_events'],returned['meaningful_events']),(1,0,1))
  events=self.events();gone=events[1][1]
  self.assertEqual(gone['diff'],{'source_present':{'before':True,'after':False}})
  self.assertEqual(gone['task_status'],'funded');self.assertFalse(gone['cash_opportunity_verified'])
  self.assertEqual(gone['evidence']['presence_reason'],'ABSENT_FROM_COMPLETE_SOURCE_LISTING_UNVERIFIED')
  self.assertEqual(events[2][1]['diff'],{'source_present':{'before':False,'after':True}})
 def test_real_partition_transition_is_not_suppressed_by_other_partition_baseline(self):
  s=dict(self.source,urls=['https://app.squash.ci/jobs.json?a','https://app.squash.ci/jobs.json?b'])
  self.run_probe(self.response([self.job()]),sources=[s])
  self.run_probe(self.response([dict(self.job(),amount_minor=600)]),1601,[s])
  self.run_probe(self.response([self.job()]),2202,[s])
  r=self.run_probe(self.response([self.job()]),2803,[s])
  self.assertEqual(r['meaningful_events'],1)
  self.assertEqual(self.events()[-1][1]['diff']['amount_minor'],{'before':600,'after':500})
 def test_same_real_transition_seen_by_overlapping_partitions_is_deduped(self):
  s=dict(self.source,urls=['https://app.squash.ci/jobs.json?a','https://app.squash.ci/jobs.json?b'])
  self.run_probe(self.response([self.job()]),sources=[s])
  self.run_probe(self.response([self.job()]),1601,[s])
  changed=self.response([dict(self.job(),amount_minor=600)])
  a=self.run_probe(changed,2202,[s]);b=self.run_probe(changed,2803,[s])
  self.assertEqual((a['meaningful_events'],b['new_events']),(1,0))
 def test_omitted_required_pr_draft_is_malformed_and_preserves_cache(self):
  s=dict(self.source,kind='github_pulls',account='a')
  item=self.gh_item(html_url='https://github.com/a/b/pull/1',head={'sha':'a'},draft=True)
  self.run_probe((200,{},json.dumps([item]).encode()),sources=[s]);del item['draft']
  r=self.run_probe((200,{},json.dumps([item]).encode()),1601,[s])
  self.assertEqual(r['sources'][0]['status'],'ERROR');self.assertEqual(r['new_events'],0)
 def test_optional_meaningful_field_omission_has_explicit_unknown_diff(self):
  s=dict(self.source,kind='github_search')
  self.run_probe(self.gh_response([self.gh_item(merged_at='2026-01-01T00:00:00Z')]),sources=[s])
  r=self.run_probe(self.gh_response([self.gh_item()]),1601,[s]);self.assertEqual(r['meaningful_events'],1)
  self.assertEqual(self.events()[-1][1]['diff'],{'merged_at':{'before':'2026-01-01T00:00:00Z','after':None}})
 def test_receipts_retain_coverage_when_not_due_or_fetch_failed(self):
  self.run_probe(self.response([self.job()]))
  skipped=self.run_probe(self.response([]),1001)
  failed=self.run_probe((503,{},b''),1601)
  for receipt in (skipped,failed):
   source=receipt['sources'][0]
   self.assertEqual(source.get('coverage',{}).get('returned'),1)
   self.assertEqual(source['coverage']['scope'],'EXACT_SOURCE_QUERY_PARTITION_PAGE')
   self.assertEqual(source['coverage']['checked_at'],'1970-01-01T00:16:40+00:00')
   self.assertEqual(source['cursor']['rotation'],1)
 def test_repository_issues_discover_unlabelled_tasks_and_filter_prs(self):
  source=dict(self.source,kind='github_issues',limit=2,url='https://api.github.com/repos/a/b/issues?state=open&per_page=2')
  issue=self.gh_item(labels=[])
  pr=self.gh_item(id=11,html_url='https://github.com/a/b/pull/2',pull_request={})
  r=self.run_probe((200,{},json.dumps([issue,pr]).encode()),sources=[source])
  self.assertEqual(r['sources'][0]['status'],'OK')
  self.assertEqual(r['new_events'],1)
  self.assertTrue(r['sources'][0]['has_more'])
  self.assertFalse(r['sources'][0]['api_result_cap'])
  event=self.events()[0][1]
  self.assertEqual(event['task_id'],'github:a/b:issue:1')
  self.assertIsNone(event['amount_minor'])
  self.assertEqual(event['amount_status'],'UNKNOWN')
  self.assertTrue(event['qualification_required'])
  self.assertFalse(event['cash_opportunity_verified'])
  self.assertFalse(event['dispatch_allowed']);self.assertFalse(event['publish_allowed'])
  self.assertEqual(event['kind'],'BASELINE_UNTRIAGED')
 def test_repository_issue_pr_only_page_still_advances_and_retries_exact_page(self):
  source=dict(self.source,kind='github_issues',limit=1,url='https://api.github.com/repos/a/b/issues?state=open&per_page=1')
  pr=self.gh_item(html_url='https://github.com/a/b/pull/1',pull_request={})
  r=self.run_probe((200,{},json.dumps([pr]).encode()),sources=[source])
  self.assertEqual(r['sources'][0]['status'],'OK')
  self.assertEqual(self.state()['pages'],{'0':2})
  r=self.run_probe((429,{'retry-after':'4000'},b''),1601,[source])
  self.assertEqual(r['sources'][0]['status'],'ERROR')
  self.assertEqual(self.state()['pages'],{'0':2})
  seen=[]
  def transport(chosen,headers):
   seen.append(chosen['url']);return (200,{},json.dumps([self.gh_item()]).encode())
  self.p.run(self.db,{'canonical_page':'page_existing','sources':[source]},'fixture',10000,transport)
  self.assertIn('page=2',seen[0])
  self.assertEqual(self.events()[0][1]['kind'],'BASELINE_UNTRIAGED')
 def test_fifth_due_source_is_served_next_pass_before_repeated_sources(self):
  sources=[dict(self.source,id=str(i),interval=1) for i in range(5)]
  manifest={'canonical_page':'page_existing','sources':sources}
  seen=[]
  def transport(source,headers):seen.append(source['id']);return self.response([])
  self.p.run(self.db,manifest,'fixture',1000,transport)
  seen.clear()
  r=self.p.run(self.db,manifest,'fixture',1002,transport)
  self.assertEqual(seen,['4','0','1','2'])
  self.assertEqual(r['source_budget']['deferred_source_ids'],['3'])
 def test_not_due_sources_do_not_spend_read_budget(self):
  sources=[dict(self.source,id=str(i)) for i in range(6)]
  manifest={'canonical_page':'page_existing','sources':sources}
  seen=[]
  def transport(source,headers):seen.append(source['id']);return self.response([])
  self.p.run(self.db,manifest,'fixture',1000,transport)
  seen.clear()
  r=self.p.run(self.db,manifest,'fixture',1001,transport)
  self.assertEqual(seen,['4','5'])
  self.assertEqual(r['source_budget']['processed'],2)
  self.assertEqual(r['source_budget']['deferred_source_ids'],[])
  self.assertEqual(r['result'],'NO_CHANGE')
 def test_configured_read_budget_rotates_failed_sources_after_backoff(self):
  sources=[dict(self.source,id=str(i),interval=1) for i in range(3)]
  manifest={'canonical_page':'page_existing','sources':sources,'max_source_reads':1}
  seen=[]
  def transport(source,headers):seen.append(source['id']);return (503,{},b'')
  for now in (1000,1003,1006,1009):self.p.run(self.db,manifest,'fixture',now,transport)
  self.assertEqual(seen,['0','1','2','0'])
  self.assertEqual(self.state('0')['partition'],0)
  self.assertEqual(self.state('0')['last_checked'],1009)
 def test_invalid_read_budget_is_rejected_without_network_reads(self):
  for budget in (0,-1,True,'4',1.5):
   with self.subTest(budget=budget),self.assertRaises(ValueError):
    self.p.run(self.db,{'canonical_page':'page_existing','sources':[self.source],'max_source_reads':budget},'fixture',1000,lambda *_:self.fail('network must not run'))
 def test_manifest_includes_direct_known_repository_issue_discovery(self):
  manifest=json.loads(Path(__file__).with_name('sources.json').read_text())
  direct=[source for source in manifest['sources'] if source['kind']=='github_issues']
  self.assertTrue(direct,'direct issues discovery missing')
  urls=[url for source in direct for url in source.get('urls',[source['url']])]
  for repo in ('limitlessprojects/Go-Hybrid','AndreaBozzo/dataprof','rtk-ai/rtk'):
   self.assertTrue(any('/repos/'+repo+'/issues?' in url and 'state=open' in url for url in urls),repo)
  self.assertTrue(all('label' not in url and '/search/' not in url for url in urls))
if __name__=='__main__': unittest.main()
