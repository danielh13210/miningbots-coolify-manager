function stop_instance(instance,completion_handler){
    fetch(`${location.origin}/stop?instance=${instance}`,{method:'POST'}).then((resp)=>{
        const success=resp.ok;
        completion_handler(success);
    })
}

function delete_instance(instance,completion_handler){
    fetch(`${location.origin}/delete?instance=${instance}`,{method:'DELETE'}).then((resp)=>{
        const success=resp.ok;
        completion_handler(success);
    })
}

function start_instance(instance,completion_handler){
    fetch(`${location.origin}/start?instance=${instance}`,{method:'POST'}).then((resp)=>{
        const success=resp.ok;
        completion_handler(success);
    })
}

function delete_player(player,instance,completion_handler){
    fetch(`${location.origin}/players/delete?instance=${instance}&player=${player}`,{method:'DELETE'}).then((resp)=>{
        const success=resp.ok;
        completion_handler(success);
    })
}

function stop_instance_clicked(instance){
    if(!confirm(`Are you sure you want to stop instance "${instance}"?`))return;
    let button=document.getElementById(`stop-instance-${instance}`);
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

function start_instance_clicked(instance){
    if(!confirm(`Are you sure you want to start instance "${instance}"?`))return;
    let button=document.getElementById(`start-instance-${instance}`);
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

function delete_instance_clicked(instance){
    if(!confirm(`Are you sure you want to delete instance "${instance}"?`))return;
    let button=document.getElementById(`delete-instance-${instance}`);
    button.disabled=true;
    button.innerText="Deleting...";
    delete_instance(instance,(success)=>{
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

function delete_player_clicked(player,instance){
    if(!confirm(`Are you sure you want to delete player "${player}"?`))return;
    let button=document.getElementById(`delete-player-${player}`);
    button.disabled=true;
    button.innerText="Deleting...";
    delete_player(player,instance,(success)=>{
        if (success) {
            location.reload();
        } else {
            button.innerText="Failed";
            setTimeout(()=>{
                button.disabled=false;
                button.innerText="Delete";
            },2000);
        }
    });
}
