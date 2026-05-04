// Elements
const liveBadge = document.getElementById('liveBadge');
const peakListenersEl = document.getElementById('peakListenersCount');
const programEl = document.getElementById('playerSub'); // Now using playerSub in the bar
const nextProgramEl = document.getElementById('nextProgram');
const playerThumb = document.getElementById('playerThumb');

let lastCurrentProgram = null;

/**
 * Get current and next program from schedule
 */
async function getAcaraSekarang() {
    const data = await API.getSchedule();
    if (!data) return { current: "Siaran Langsung VoT", next: null };

    const { day, time } = UI.getWaktuBali();
    const jadwalHariIni = data.jadwal[day];

    if (!jadwalHariIni || jadwalHariIni.length === 0) {
        return { current: "Siaran Langsung VoT", next: null };
    }

    let currentAcara = "Siaran Langsung VoT";
    let nextAcara = null;

    for (let i = 0; i < jadwalHariIni.length; i++) {
        const p = jadwalHariIni[i];
        const mulai = p.waktu_mulai;
        const selesai = p.waktu_selesai || "23:59";

        if (time >= mulai && time < selesai) {
            currentAcara = p.acara;
            if (i + 1 < jadwalHariIni.length) {
                nextAcara = `${jadwalHariIni[i + 1].acara} (${jadwalHariIni[i + 1].waktu_mulai})`;
            }
            break;
        } else if (time < mulai) {
            if (!nextAcara) {
                nextAcara = `${p.acara} (${p.waktu_mulai})`;
            }
        }
    }
    return { current: currentAcara, next: nextAcara };
}

/**
 * Animate program change
 */
function animateProgramChange(newCurrent, newNext) {
    programEl.classList.remove('text-pulse');
    programEl.style.transition = "all 0.8s ease-in-out";
    nextProgramEl.style.transition = "all 0.8s ease-in-out";

    programEl.style.opacity = "0";
    programEl.style.transform = "translateY(-15px)";
    nextProgramEl.style.transform = "translateY(-18px)";

    setTimeout(() => {
        programEl.style.transition = "none";
        nextProgramEl.style.transition = "none";

        programEl.textContent = newCurrent;
        if (newNext) {
            nextProgramEl.textContent = `Selanjutnya: ${newNext}`;
            nextProgramEl.style.display = "block";
        } else {
            nextProgramEl.style.display = "none";
        }

        programEl.style.transform = "translateY(15px)";
        nextProgramEl.style.opacity = "0";
        nextProgramEl.style.transform = "translateY(15px)";

        void programEl.offsetWidth;

        programEl.style.transition = "all 0.8s ease-out";
        nextProgramEl.style.transition = "all 0.8s ease-out";

        programEl.style.opacity = "1";
        programEl.style.transform = "translateY(0)";
        nextProgramEl.style.opacity = "1";
        nextProgramEl.style.transform = "translateY(0)";

        setTimeout(() => {
            programEl.style.transition = "";
            programEl.style.opacity = "";
            programEl.style.transform = "";
            nextProgramEl.style.transition = "";
            nextProgramEl.style.opacity = "";
            nextProgramEl.style.transform = "";
            programEl.classList.add('text-pulse');
        }, 800);
    }, 800);
}

/**
 * Check radio status and update UI
 */
async function checkRadioStatus() {
    const data = await API.getRadioStatus();

    if (data && data.streamstatus === 1) {
        liveBadge.innerHTML = '<span class="pulse"></span> LIVE NOW';
        liveBadge.className = 'live-badge online';

        if (data.currentlisteners !== undefined) {
            peakListenersEl.textContent = UI.formatNumber(data.currentlisteners) + ' Mendengarkan';
        }

        const jadwal = await getAcaraSekarang();

        if (lastCurrentProgram !== null && lastCurrentProgram !== jadwal.current) {
            animateProgramChange(jadwal.current, jadwal.next);
        } else if (lastCurrentProgram === null) {
            programEl.textContent = jadwal.current;
            programEl.classList.add('text-pulse');
            if (jadwal.next) {
                nextProgramEl.textContent = `Selanjutnya: ${jadwal.next}`;
                nextProgramEl.style.display = "block";
            }
        } else if (jadwal.next) {
            nextProgramEl.textContent = `Selanjutnya: ${jadwal.next}`;
            nextProgramEl.style.display = "block";
        }

        lastCurrentProgram = jadwal.current;
        playerThumb.classList.remove('thumb-offline');
        playerThumb.classList.add('thumb-online');
    } else {
        setOfflineState();
    }
}

function setOfflineState() {
    liveBadge.innerHTML = '<i class="fa-solid fa-circle-exclamation" style="margin-right: 5px;"></i> OFFLINE';
    liveBadge.className = 'live-badge offline';
    peakListenersEl.textContent = '— Mendengarkan';
    programEl.textContent = 'Sedang Offline...';
    programEl.classList.remove('text-pulse');
    nextProgramEl.style.display = "none";
    playerThumb.classList.remove('thumb-online');
    playerThumb.classList.add('thumb-offline');
    lastCurrentProgram = null;
}

// Initialize
checkRadioStatus();
setInterval(checkRadioStatus, 30000);
