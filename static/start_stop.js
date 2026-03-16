function stop(instance,completion_handler){
    fetch(`${location.origin}/stop?instance=${instance}`,{method:'POST'}).then((resp)=>{
        const success=resp.ok;
        completion_handler(success);
    })
}

function delete_inst(instance,completion_handler){
    fetch(`${location.origin}/delete?instance=${instance}`,{method:'DELETE'}).then((resp)=>{
        const success=resp.ok;
        completion_handler(success);
    })
}

function start(instance,completion_handler){
    fetch(`${location.origin}/start?instance=${instance}`,{method:'POST'}).then((resp)=>{
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
    });
}

function start_clicked(instance){
    if(!confirm(`Are you sure you want to start instance "${instance}"?`))return;
    let button=document.getElementById(`start-${instance}`);
    button.disabled=true;
    button.innerText="Starting...";
    start(instance,(success)=>{
        if (success) {
            location.reload();
        } else {
            button.innerText="Failed";
            setTimeout(()=>{
                button.disabled=false;
                button.innerText="Start";
            },2000);
        }
    });
}

function delete_clicked(instance){
    if(!confirm(`Are you sure you want to delete instance "${instance}"?`))return;
    let button=document.getElementById(`delete-${instance}`);
    button.disabled=true;
    button.innerText="Deleting...";
    delete_inst(instance,(success)=>{
        if (success) {
            location.href='/';
        } else {
            button.innerText="Failed";
            setTimeout(()=>{
                button.disabled=false;
                button.innerText="Delete";
            },2000);
        }
    });
}
