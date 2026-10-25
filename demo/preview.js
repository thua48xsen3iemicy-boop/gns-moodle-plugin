/* Local-only interaction preview. No authentication or IP enforcement. */
(() => {
    'use strict';

    const form = document.getElementById('demoform');
    const login = document.getElementById('loginpanel');
    const workspace = document.getElementById('workspace');
    const feedback = document.getElementById('feedback');
    const captchaLabel = document.getElementById('captchalabel');
    let answer = 0;

    function newChallenge() {
        const left = Math.floor(Math.random() * 8) + 2;
        const right = Math.floor(Math.random() * 7) + 1;
        answer = left + right;
        captchaLabel.textContent = `Проверка: ${left} + ${right} = ?`;
        form.elements.captcha.value = '';
    }

    form.addEventListener('submit', event => {
        event.preventDefault();
        const data = new FormData(form);
        const ip = data.get('clientip');
        const allowed = ip === '192.0.2.10' || ip === '198.51.100.20';
        const credentials = data.get('username') === 'demo' && data.get('password') === 'demo123';
        const challenge = Number(data.get('captcha')) === answer && data.get('captcha') !== '';

        if (!allowed) {
            feedback.textContent = 'Этот тестовый IP-адрес не входит в список доступа.';
        } else if (!credentials) {
            feedback.textContent = 'Неверные тестовые логин или пароль.';
        } else if (!challenge) {
            feedback.textContent = 'Проверка не пройдена. Решите новый пример.';
        } else {
            form.elements.password.value = '';
            login.hidden = true;
            workspace.hidden = false;
            feedback.textContent = '';
            return;
        }
        form.elements.password.value = '';
        newChallenge();
    });

    document.getElementById('logout').addEventListener('click', () => {
        workspace.hidden = true;
        login.hidden = false;
        form.reset();
        newChallenge();
        document.getElementById('username').focus();
    });

    newChallenge();
})();
