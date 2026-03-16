function stop(instance,completion_handler){
    fetch(`${location.origin}/stop?instance=${instance}`,{method:'POST'}).then((resp)=>{
        const success=resp.ok;
        completion_handler(success);
    })
}

function stop_clicked(instance){
    if(!confirm(`Are you sure you want to stop instance "${instance}"?`))return;
    let button=document.getElementById(`stop-${instance}`);
    button.disabled=true;
    button.innerText="Stopping...";
    stop(instance,(success)=>{
        if (success) {
            location.reload();
        } else {
            button.innerText="Failed";
            setTimeout(()=>{
                button.disabled=false;
                button.innerText="Stop";
            },2000);
        }
    })
}
