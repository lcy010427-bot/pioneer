/* Calendar-based, read-only aggregation. Also loaded by Node tests without a browser. */
(function(root){
 'use strict';
 const DAY=86400000;
 function parse(value){if(!/^\d{4}-\d{2}-\d{2}$/.test(value||''))return null;const d=new Date(value+'T00:00:00Z');return Number.isFinite(+d)&&iso(d)===value?d:null;}
 function iso(d){return d.toISOString().slice(0,10);}
 function addDays(value,n){return iso(new Date(+parse(value)+n*DAY));}
 function shiftMonths(value,n){const d=parse(value);const target=new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+n,1));const last=new Date(Date.UTC(target.getUTCFullYear(),target.getUTCMonth()+1,0)).getUTCDate();target.setUTCDate(Math.min(d.getUTCDate(),last));return iso(target);}
 function preset(kind,end){if(!parse(end)||!['5y','1y','1q'].includes(kind))throw Error('유효하지 않은 기간입니다.');return {start:addDays(shiftMonths(end,-({'5y':60,'1y':12,'1q':3}[kind])),1),end,unit:({'5y':'quarter','1y':'month','1q':'week'}[kind])};}
 function validate(start,end,unit){if(!parse(start)||!parse(end)||start>end||start<'1900-01-01'||!['quarter','month','week'].includes(unit)||+parse(end)-parse(start)>366*20*DAY)throw Error('1900년 이후, 최대 20년의 올바른 시작·종료일을 선택하세요.');}
 function floor(value,unit){const d=parse(value);if(unit==='week')return addDays(value,-((d.getUTCDay()+6)%7));return iso(new Date(Date.UTC(d.getUTCFullYear(),unit==='quarter'?Math.floor(d.getUTCMonth()/3)*3:d.getUTCMonth(),1)));}
 function next(value,unit){return unit==='week'?addDays(value,7):shiftMonths(value,unit==='quarter'?3:1);}
 function coverage(c){if(!c.run)return null;const match=(c.run.query||'').match(/FIRST_PDATE:\[(\d{4}-\d{2}-\d{2}) TO (\d{4}-\d{2}-\d{2})\]/);const start=match?match[1]:`${c.run.start_year}-01-01`;let end=match?match[2]:`${c.run.end_year}-12-31`;if(parse((c.run.finished_at||'').slice(0,10)))end=[end,c.run.finished_at.slice(0,10)].sort()[0];return {start,end,complete:c.run.status==='complete'};}
 function inRange(p,range){if(parse(p.date))return p.date>=range.start&&p.date<=range.end;return Number.isInteger(p.year)&&p.year>=Number(range.start.slice(0,4))&&p.year<=Number(range.end.slice(0,4));}
 function series(c,range){
  validate(range.start,range.end,range.unit);
  const cov=coverage(c), bins=[],unknown=c.papers.filter(p=>!parse(p.date)&&inRange(p,range));
  let cursor=floor(range.start,range.unit);
  while(cursor<=range.end){const after=next(cursor,range.unit),calendarEnd=addDays(after,-1);const start=[cursor,range.start].sort()[1],end=[calendarEnd,range.end].sort()[0];
   const overlaps=!!cov&&cov.start<=end&&cov.end>=start;
   const records=c.papers.filter(p=>parse(p.date)&&p.date>=start&&p.date<=end&&overlaps&&p.date>=cov.start&&p.date<=cov.end).map(p=>p.record_key);
   const unknownHere=unknown.some(p=>p.year>=Number(start.slice(0,4))&&p.year<=Number(end.slice(0,4)));
   const covered=overlaps&&cov.complete&&cov.start<=start&&cov.end>=end;
   const incomplete=start!==cursor||end!==calendarEnd;
   const year=cursor.slice(0,4),month=Number(cursor.slice(5,7));
   bins.push({id:start,start,end,calendar_start:cursor,calendar_end:calendarEnd,label:range.unit==='quarter'?`${year} Q${Math.floor((month-1)/3)+1}`:range.unit==='month'?`${year}.${String(month).padStart(2,'0')}`:start.slice(5).replace('-','/'),
    count:overlaps&&(!unknownHere||records.length)?records.length:null,records,covered,incomplete,unknown_dates:unknownHere,
    partial:!covered||incomplete||unknownHere});cursor=after;
  }
  const known=[...new Set(bins.flatMap(b=>b.records))];
  return {bins,records:known,count:known.length,unknown_dates:unknown.length,
   fully_covered:!!cov&&cov.complete&&cov.start<=range.start&&cov.end>=range.end,coverage:cov};
 }
 const api={parse,iso,addDays,shiftMonths,preset,validate,coverage,inRange,series};
 if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.PioneerTimeline=api;
})(typeof globalThis!=='undefined'?globalThis:this);
