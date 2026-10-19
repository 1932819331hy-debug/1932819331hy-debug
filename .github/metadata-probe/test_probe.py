"""Boundary regressions: real SQLite persistence, isolated HTTP responses only."""
import importlib.util,json,tempfile,unittest
from pathlib import Path
SPEC=importlib.util.spec_from_file_location('probe',Path(__file__).with_name('bounty_probe.py'))
class ProbeTests(unittest.TestCase):
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
if __name__=='__main__': unittest.main()
