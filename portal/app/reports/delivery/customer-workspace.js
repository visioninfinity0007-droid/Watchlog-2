"use client";
import {withSite} from "../../site-context";
import {OwnerPage,Lead,Notice,Loading} from "../../owner/ui";
import useDelivery from "./use-delivery";
import RecipientForm from "./recipient-form";
import RecipientList from "./recipient-list";
import DeliveryHistory from "./delivery-history";

export default function CustomerDelivery(){
  const d=useDelivery();
  const active=d.recipients.filter(r=>r.enabled).length;
  const latest=d.history[0];

  let tone="unknown",title="No one receives reports yet",body="Add a recipient to start report delivery.";
  if(d.recipients.length&&!active){title="Report delivery is paused";body="Every recipient is paused. Resume one to restart delivery.";tone="warn"}
  else if(active){
    title=active===1?"Reports go to 1 recipient":"Reports go to "+active+" recipients";
    if(!latest){body="No delivery has been recorded yet.";tone="unknown"}
    else if(latest.status==="failed"){title="The latest report delivery failed";body=[latest.date,latest.site].filter(Boolean).join(" · ")+". Check the recipient's details below.";tone="warn"}
    else if(latest.status==="sent"){body="Latest delivery sent"+(latest.date?" on "+latest.date:"")+".";tone="ok"}
    else{body="Latest delivery is "+String(latest.status||"not confirmed").toLowerCase()+".";tone="unknown"}
  }

  return <OwnerPage active="Reports" email={d.email} siteId={d.siteId}
    kicker={["Reports","Delivery"]}
    title="Report delivery"
    actions={<a className="ow-btn quiet" href={withSite("/reports/",d.siteId)}>Back to report</a>}>
    {d.error&&<div style={{marginBottom:14}}><Notice tone="bad">{d.error}</Notice></div>}
    {!d.loaded?<Loading label="Loading report delivery"/>:<>
      {!d.error&&<Lead tone={tone} title={title} body={body}/>}
      <RecipientList delivery={d}/>
      <RecipientForm delivery={d}/>
      <DeliveryHistory items={d.history}/>
    </>}
  </OwnerPage>;
}
