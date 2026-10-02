"use client";
import {Section,Status,Empty} from "../../owner/ui";
function channelLabel(v){return v==="whatsapp"?"WhatsApp":v==="both"?"Email + WhatsApp":"Email"}

export default function RecipientList({delivery}){
  const canManage=delivery.role==="owner"||delivery.role==="admin";
  const list=delivery.recipients;
  return <Section first title="Recipients" count={list.length||null}>
    {list.length?<table className="ow-table">
      <thead><tr><th>Recipient</th><th>Send by</th><th>Site</th><th>Status</th>{canManage&&<th aria-label="Actions"/>}</tr></thead>
      <tbody>{list.map(r=><tr key={r.id}>
        <td><b>{r.name||"Recipient"}</b></td>
        <td>{channelLabel(r.channel)}</td>
        <td>{r.site||"All sites"}</td>
        <td><Status tone={r.enabled?"ok":"unknown"}>{r.enabled?"Active":"Paused"}</Status></td>
        {canManage&&<td><div style={{display:"inline-flex",gap:8,flexWrap:"wrap"}}>
          <button type="button" className="ow-btn small quiet" onClick={()=>delivery.toggle(r.id,r.enabled)}>{r.enabled?"Pause":"Resume"}</button>
          <button type="button" className="ow-btn small danger" onClick={()=>delivery.remove(r.id)}>Remove</button>
        </div></td>}
      </tr>)}</tbody>
    </table>:<Empty title="No report recipients yet">{canManage?"Add the people who should receive WatchLog reports.":"An account owner or admin can add report recipients."}</Empty>}
  </Section>;
}
