from test_complete_flow import cmd,role

def test_correction_preserves_progress_and_requires_latest_ack(env):
    _,c,b,h,join,step=env;p=join();sid=step();c.post(b+'/status',headers=p,json={'step':sid,'state':'done'})
    one=cmd(c,b,h,'step.correct',{'step':sid,'title':'新标题','criteria':'新标准','note':'修正命令','version':0});assert one.status_code==200,one.text
    state=c.get(b,headers=p).json();assert state['steps'][0]['mine']=='done' and len(state['steps'])==1
    two=cmd(c,b,h,'step.correct',{'step':sid,'title':'第二次','criteria':'再次修正','note':'补充','version':1});assert two.status_code==200
    assert cmd(c,b,p,'correction.ack',{'id':one.json()['id']}).status_code==409
    assert cmd(c,b,p,'correction.ack',{'id':two.json()['id']}).status_code==200
    assert cmd(c,b,p,'correction.ack',{'id':two.json()['id']}).status_code==200
    state=c.get(b,headers=h).json();assert state['steps'][0]['corrections'][-1]['acknowledged']==1
    assert state['steps'][0]['corrections'][0]['previous']['title']=='打开首页'
    step('下一步')
    assert cmd(c,b,h,'step.correct',{'step':sid,'title':'不能更正历史','criteria':'x','note':'x'}).status_code==409

def test_clarification_never_changes_operation_progress(env):
    _,c,b,h,join,step=env;p=join();q=join();sid=step()
    iid=cmd(c,b,p,'issue.clarify',{'step':sid}).json()['id']
    assert c.get(b,headers=p).json()['steps'][0]['mine']=='waiting'
    assert cmd(c,b,q,'issue.agree',{'id':iid}).status_code==200
    assert c.get(b,headers=q).json()['steps'][0]['mine']=='waiting'
    assert cmd(c,b,h,'issue.answer',{'id':iid,'body':'请看标准中的终端输出'}).status_code==200
    answer=c.get(b,headers=p).json()['steps'][0]['issues'][0]['answers'][-1]['id']
    assert cmd(c,b,p,'clarification.ack',{'id':iid,'answer':answer}).status_code==200
    view=c.get(b,headers=p).json()['steps'][0];assert view['issues'][0]['my_ack'] and view['mine']=='waiting'
    assert view['issues'][0]['kind']=='clarification'

def test_private_details_and_environment_answers(env):
    _,c,b,h,join,step=env;p=join();q=join();sid=step()
    iid=cmd(c,b,p,'issue.add',{'step':sid,'title':'下载失败'}).json()['id']
    assert cmd(c,b,p,'issue.details',{'id':iid,'environment':'windows','error_code':'PRIVATE-403','attempted':'只给工作人员看','version':1}).status_code==200
    assert 'PRIVATE-403' in c.get(b,headers=h).text
    assert 'PRIVATE-403' not in c.get(b,headers=q).text
    assert cmd(c,b,q,'issue.details',{'id':iid,'error_code':'冒充'}).status_code==403
    cmd(c,b,h,'issue.answer',{'id':iid,'body':'Windows 专用方案','environment':'windows'})
    cmd(c,b,h,'issue.answer',{'id':iid,'body':'macOS 专用方案','environment':'macos'})
    cmd(c,b,p,'person.environment',{'environment':'windows'})
    view=c.get(b,headers=p).text;assert 'Windows 专用方案' in view and 'macOS 专用方案' not in view

def test_skip_denominator_approval_and_frozen_history(env):
    _,c,b,h,join,step=env;p=join();q=join();sid=step()
    pid=c.get(b,headers=p).json()['me']['id']
    assert cmd(c,b,p,'skip.request',{'step':sid,'reason':'旁听'}).status_code==200
    assert c.get(b,headers=h).json()['steps'][0]['total']==2
    assert cmd(c,b,p,'skip.decide',{'step':sid,'person':pid,'decision':'approved','version':1}).status_code==403
    assert cmd(c,b,h,'skip.decide',{'step':sid,'person':pid,'decision':'approved','version':1}).status_code==200
    view=c.get(b,headers=h).json()['steps'][0];assert view['total']==1 and view['excluded']==1
    assert cmd(c,b,p,'status',{'step':sid,'state':'done','version':0}).status_code==409
    step('下一步')
    view=c.get(b,headers=h).json();assert view['steps'][0]['total']==1 and view['steps'][1]['total']==2
    assert not any(t['person']==pid for t in view['tails'])
    assert cmd(c,b,h,'skip.decide',{'step':sid,'person':pid,'decision':'rejected','version':2}).status_code==409

def test_pulse_is_rate_limited_idempotent_and_preserves_done(env):
    _,c,b,h,join,step=env;p=join();sid=step();c.post(b+'/status',headers=p,json={'step':sid,'state':'done'})
    pid=cmd(c,b,h,'pulse.start',{'step':sid}).json()['id']
    assert cmd(c,b,h,'pulse.start',{'step':sid}).status_code==429
    assert cmd(c,b,p,'pulse.ack',{'id':pid}).status_code==200
    assert cmd(c,b,p,'pulse.ack',{'id':pid}).status_code==200
    view=c.get(b,headers=p).json()['steps'][0];assert view['mine']=='done' and view['pulse']['confirmed']==1
    assert cmd(c,b,p,'person.reminders',{'enabled':False}).status_code==200
    assert c.get(b,headers=p).json()['me']['reminders'] is False
    step('后一步');assert cmd(c,b,p,'pulse.ack',{'id':pid}).status_code==409

def test_feature_permissions_and_cross_room_checks(env):
    _,c,b,h,join,step=env;p=join();sid=step();rr,screen=role(c,b,h,'screen')
    for action,data in [('step.correct',{'step':sid,'title':'x','criteria':'x','note':'x'}),('issue.clarify',{'step':sid}),('pulse.start',{'step':sid}),('skip.request',{'step':sid,'reason':'x'})]:
        assert cmd(c,b,screen,action,data).status_code==403
    other=c.post('/api/rooms',json={'title':'别的房间'}).json();ob='/api/rooms/'+other['code'];oh={'Authorization':other['token']}
    correction=cmd(c,b,h,'step.correct',{'step':sid,'title':'新','criteria':'新','note':'新','version':0}).json()['id']
    assert cmd(c,ob,oh,'correction.ack',{'id':correction}).status_code==403
