import Mark from "../mark";
export default function CustomerMessage({message,styles}){return <div className={`${styles.message} ${message.role==="user"?styles.user:styles.assistant}`}><div className={styles.avatar}>{message.role==="user"?"You":<Mark size={24}/>}</div><div className={styles.messageBody}><div className={styles.messageText}>{message.content}</div></div></div>}
