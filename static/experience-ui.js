/* Pure presentation decisions used by the live console and participant page. */
function issueNeedsAttention(i){if(i.stage==='closed')return false;if(i.kind==='clarification')return !i.answers.length||(i.acknowledged||0)<i.count;return i.affected>0}
function nextParticipantAction(s){
 if(!connected)return '连接恢复后再反馈，未同步的操作不会显示为已完成。';
 if(state.closed||s.phase==='frozen')return '当前是历史记录；需要继续操作时请返回当前检查点。';
 if(s.skips?.some(x=>x.decision==='approved'))return '讲解者已批准本步暂不参与；无需提交本步进度，下一步会重新加入。';
 const correction=s.corrections?.at(-1);if(correction&&!correction.mine)return '先阅读本步更正说明，再确认收到；你原来的进度没有被重置。';
 const mine=allIssues().filter(i=>i.mine&&i.my_active&&i.kind!=='clarification');
 if(mine.some(i=>i.answers.length))return '你关注的问题有处理办法：先按说明尝试，奏效后确认恢复。';
 if(mine.length||s.mine==='stuck')return '你已报告受阻。可以补充错误信息，等待讲解者或助教回应。';
 if(s.mine==='done')return '本步已经完成，可以等待下一步，或展开可选任务。';
 if(s.mine==='working')return '继续按完成标准操作；做完后点完成，遇到问题随时点卡住了。';
 return '先按完成标准开始操作，再选择进行中、完成或卡住了。';
}
function participantGuidance(s){return `<div class="next-action" role="status"><span class="eyebrow">接下来</span><p>${esc(nextParticipantAction(s))}</p></div>`}
function clarificationCard(i,staff){const ans=i.answers.at(-1);return `<article class="issue-card"><div class="section-head"><h3>要求澄清：${esc(i.title)}</h3>${i.old?'<span class="pill warning">前序问题</span>':''}</div><p class="muted">${i.count} 人有同样疑问 · ${i.acknowledged||0} 人确认看到答复</p>${i.answers.map(answerCard).join('')}<div class="actions">${state.closed?'':staff?button('发布澄清答复','answer',i.id)+button(i.assignee===state.me.id?'释放任务':'我来处理',i.assignee===state.me.id?'unclaim':'claim',i.id):(!i.old&&!i.mine?button('我也想了解','agree',i.id):'')+(i.mine&&ans&&!i.my_ack?button('已看到澄清答复','clarify-ack',i.id):'')}</div><small>这是对要求的疑问，不计为操作失败。${i.old?' 来源：'+esc(i.stepTitle):''}</small></article>`}
