export default function CustomerComposer({chat,styles}){
  return <footer className={styles.composerWrap}>
    {chat.attachments.length>0&&<div className={styles.attachmentStrip}>
      {chat.attachments.map(a=><div className={styles.attachmentChip} key={a.id}>
        <img src={a.preview_url} alt="" />
        <span>{a.name}</span>
        <button type="button" aria-label={"Remove "+a.name} onClick={()=>chat.removeAttachment(a.id)}>×</button>
      </div>)}
    </div>}
    <div className={styles.composer}>
      <label className={styles.attachButton} title="Attach image" aria-label="Attach image">
        <span>+</span>
        <input type="file" accept="image/jpeg,image/png,image/webp" multiple
          onChange={e=>{chat.addAttachments(e.target.files);e.target.value=""}} />
      </label>
      <textarea
        ref={chat.inputRef}
        rows={1}
        value={chat.draft}
        onChange={e=>chat.setDraft(e.target.value)}
        onKeyDown={e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();chat.send()}}}
        placeholder={"Ask WatchLog about "+(chat.site?.name||"this site")+"..."}
        aria-label="Ask WatchLog"
      />
      <button type="button" aria-label="Send"
        disabled={(!chat.draft.trim()&&!chat.attachments.length)||chat.busy}
        onClick={()=>chat.send()}>↑</button>
    </div>
    <small>WatchLog can make mistakes. Check important information.</small>
  </footer>
}
