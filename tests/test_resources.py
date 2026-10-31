from test_complete_flow import cmd,role

def test_commands_roundtrip_and_platform_fallback(env):
    _,c,b,h,join,step=env
    options={'commands':{'unspecified':{'command':'python --version','expected':'Python 3.x','note':'仅复制，不自动运行'},'windows':{'command':'py --version','expected':'Python 3.x'}}}
    added=cmd(c,b,h,'step.add',{'title':'验证版本','options':options});assert added.status_code==200
    sid=added.json()['id'];cmd(c,b,h,'step.start',{'id':sid})
    assert c.get(b+'/template',headers=h).json()['steps'][0]['options']['commands']==options['commands']|{'windows':{'command':'py --version','expected':'Python 3.x','note':''}}
    # Scripts are never executed; command text is merely stored and presented escaped.
    assert cmd(c,b,h,'step.add',{'title':'坏格式','options':{'commands':{'unknown':{'command':'x'}}}}).status_code==400

def test_resource_permissions_validation_copy_and_removal(env):
    _,c,b,h,join,step=env;p=join();rr,screen=role(c,b,h,'screen')
    data={'title':'官方安装说明','link':'https://www.python.org/','description':'按系统选择安装包'}
    assert cmd(c,b,p,'room.resource.add',data).status_code==403
    assert cmd(c,b,h,'room.resource.add',{**data,'link':'javascript:alert(1)'}).status_code==400
    assert cmd(c,b,h,'room.resource.add',data).status_code==200
    rows=c.get(b,headers=p).json()['resources'];assert rows[0]['title']==data['title']
    assert c.get(b,headers=screen).json()['resources']==rows
    copied=cmd(c,b,h,'room.copy').json()
    assert c.get('/api/rooms/'+copied['code'],headers={'Authorization':copied['token']}).json()['resources']==rows
    assert cmd(c,b,h,'room.resource.remove',{'id':rows[0]['id']}).status_code==200
    assert c.get(b,headers=p).json()['resources']==[]
