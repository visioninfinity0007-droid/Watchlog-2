"use client";
import {Section,Status,Empty} from "../../owner/ui";
function channelLabel(v){return v==="whatsapp"?"WhatsApp":v==="both"?"Email + WhatsApp":"Email"}
function statusLabel(v){return v==="sent"?"Sent":v==="failed"?"Failed":v==="pending"?"Pending":String(v||"Unknown").replace(/^./,c=>c.toUpperCase())}
function statusTone(v){return v==="sent"?"ok":v==="failed"?"bad":"unknown"}

function Rows({items}){
  return <table className="ow-table">
    <thead><tr><th>Date</th><th>Site</th><th>Channel</th><th>Status</th></tr></thead>
    <tbody>{items.map((h,i)=><tr key={i}>
      <td>{h.date}</td>
      <td>{h.site}</td>
      <td>{channelLabel(h.channel)}</td>
      <td><Status tone={statusTone(h.status)}>{statusLabel(h.status)}</Status></td>
    </tr>)}</tbody>
  </table>;
}

export default function DeliveryHistory({items}){
  const list=items.slice(0,50);
  const recent=list.slice(0,10);
  const older=list.slice(10);
  const failed=list.filter(h=>h.status==="failed").length;
  return <Section title="Recent deliveries" count={list.length||null} note={failed?failed+" of the last "+list.length+" deliveries failed.":null}>
    {list.length?<>
      <Rows items={recent}/>
      {older.length>0&&<details className="ow-details" style={{marginTop:12}}><summary>Earlier deliveries · {older.length}</summary><div><Rows items={older}/></div></details>}
    </>:<Empty title="No deliveries yet">Delivered reports will be listed here.</Empty>}
  </Section>;
}
