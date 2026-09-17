import Mark from "../mark";
import RichText from "../rich-text";
import CustomerCard from "./customer-card";
import CustomerActions from "./customer-actions";

export default function CustomerMessage({message,siteId,styles}){
  const cards=message.payload?.cards||[],actions=message.payload?.proposed_actions||[],isUser=message.role==="user";
  return <div className={`${styles.message} ${isUser?styles.user:styles.assistant}`}>
    {!isUser&&<div className={styles.avatar}><Mark size={24}/></div>}
    <div className={styles.messageBody}>
      {isUser
        ? <div className={styles.messageText}>{message.content}</div>
        : <RichText text={message.content} className={styles.messageText}/>}
      {cards.map((c,i)=><CustomerCard card={c} siteId={siteId} styles={styles} key={i}/>)}
      <CustomerActions actions={actions} siteId={siteId} styles={styles}/>
    </div>
  </div>;
}
