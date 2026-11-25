"use strict";

(() => {
    const telegram = window.Telegram?.WebApp ?? null;

    const elements = {
        status: document.getElementById("status"),
        authError: document.getElementById("auth-error"),
        authErrorText: document.getElementById("auth-error-text"),
        content: document.getElementById("app-content"),

        profileForm: document.getElementById("profile-form"),
        profileOptions: document.getElementById("profile-options"),
        anonymousMode: document.getElementById("mode-anonymous"),
        namedMode: document.getElementById("mode-named"),
        telegramName: document.getElementById("telegram-name"),
        saveProfile: document.getElementById("save-profile"),

        myCount: document.getElementById("my-count"),

        refreshTop: document.getElementById("refresh-top"),
        ratingLoading: document.getElementById("rating-loading"),
        ratingEmpty: document.getElementById("rating-empty"),
        ratingList: document.getElementById("rating-list"),
    };

    let initData = "";
    let authenticated = false;
    let saving = false;
    let refreshing = false;

    class ApiError extends Error {
        constructor(message, status = 0) {
            super(message);
            this.name = "ApiError";
            this.status = status;
        }
    }

    function setStatus(message = "", type = "info") {
        elements.status.textContent = message;
        elements.status.dataset.type = type;
        elements.status.hidden = !message;
    }

    function showAuthError(message) {
        authenticated = false;

        elements.content.hidden = true;
        elements.authError.hidden = false;
        elements.authErrorText.textContent = message;

        elements.profileOptions.disabled = true;
        elements.saveProfile.disabled = true;
        elements.refreshTop.disabled = true;

        setStatus("");
    }

    function showStartupError(message) {
        authenticated = false;

        elements.content.hidden = true;
        elements.authError.hidden = false;

        elements.authErrorText.textContent = (
            `${message} Закрой приложение и попробуй открыть его снова.`
        );

        setStatus(message, "error");
    }

    function updateControls() {
        const disabled = !authenticated || saving;

        elements.profileOptions.disabled = disabled;
        elements.saveProfile.disabled = disabled;

        elements.refreshTop.disabled = (
            !authenticated || refreshing || saving
        );

        elements.saveProfile.textContent = (
            saving ? "Сохраняем…" : "Сохранить выбор"
        );

        elements.refreshTop.textContent = (
            refreshing ? "Загрузка…" : "Обновить"
        );
    }

    function notify(type) {
        try {
            telegram?.HapticFeedback?.notificationOccurred(type);
        } catch {
            // Виброотклик необязателен.
        }
    }

    function errorMessage(error) {
        if (error instanceof Error && error.message) {
            return error.message;
        }

        return "Произошла неизвестная ошибка.";
    }

    async function api(path, options = {}) {
        const controller = new AbortController();
        const timeout = window.setTimeout(
            () => controller.abort(),
            15000
        );

        const headers = new Headers(options.headers || {});

        headers.set("Accept", "application/json");
        headers.set("Authorization", `tma ${initData}`);

        if (options.body !== undefined) {
            headers.set("Content-Type", "application/json");
        }

        try {
            const response = await fetch(path, {
                ...options,
                headers,
                signal: controller.signal,
                cache: "no-store",
                credentials: "same-origin",
            });

            let data = null;

            try {
                data = await response.json();
            } catch {
                if (response.ok) {
                    throw new ApiError(
                        "Сервер вернул некорректный ответ.",
                        response.status
                    );
                }
            }

            if (!response.ok) {
                const detail = (
                    typeof data?.detail === "string"
                        ? data.detail
                        : `Ошибка сервера: ${response.status}.`
                );

                throw new ApiError(detail, response.status);
            }

            return data;
        } catch (error) {
            if (error instanceof ApiError) {
                throw error;
            }

            if (error?.name === "AbortError") {
                throw new ApiError(
                    "Сервер не ответил вовремя. Попробуй ещё раз."
                );
            }

            throw new ApiError(
                "Не удалось связаться с сервером. Проверь интернет."
            );
        } finally {
            window.clearTimeout(timeout);
        }
    }

    function renderProfile(profile) {
        if (
            !profile
            || typeof profile.is_anonymous !== "boolean"
            || !Number.isSafeInteger(profile.suggestions_count)
            || profile.suggestions_count < 0
        ) {
            throw new ApiError(
                "Сервер вернул некорректный профиль."
            );
        }

        const name = (
            typeof profile.telegram_name === "string"
                ? profile.telegram_name
                : "Пользователь"
        );

        elements.telegramName.textContent = name;

        elements.anonymousMode.checked = profile.is_anonymous;
        elements.namedMode.checked = !profile.is_anonymous;

        elements.myCount.textContent = (
            profile.suggestions_count.toLocaleString("ru-RU")
        );
    }

    function renderTop(data) {
        if (!data || !Array.isArray(data.items)) {
            throw new ApiError(
                "Сервер вернул некорректный рейтинг."
            );
        }

        const items = data.items.slice(0, 10);

        for (const item of items) {
            if (
                !item
                || !Number.isSafeInteger(item.position)
                || item.position < 1
                || !Number.isSafeInteger(item.suggestions_count)
                || item.suggestions_count < 0
                || typeof item.display_name !== "string"
                || typeof item.is_anonymous !== "boolean"
            ) {
                throw new ApiError(
                    "Сервер вернул некорректную строку рейтинга."
                );
            }
        }

        const fragment = document.createDocumentFragment();

        for (const item of items) {
            const row = document.createElement("li");
            row.className = "rating-item";

            const position = document.createElement("span");
            position.className = "rating-position";
            position.textContent = String(item.position);
            position.setAttribute("aria-hidden", "true");

            const name = document.createElement("span");
            name.className = "rating-name";

            if (item.is_anonymous) {
                name.classList.add("is-anonymous");
                name.textContent = "Аноним";
            } else {
                name.textContent = item.display_name;
            }

            const count = document.createElement("span");
            count.className = "rating-count";
            count.textContent = (
                item.suggestions_count.toLocaleString("ru-RU")
            );

            row.append(position, name, count);
            fragment.append(row);
        }

        // Данные пользователя вставляются через textContent,
        // а не через HTML.
        elements.ratingList.replaceChildren(fragment);

        elements.ratingEmpty.hidden = items.length > 0;
        elements.ratingLoading.hidden = true;
    }

    function handleRequestError(error) {
        if (
            error instanceof ApiError
            && (error.status === 401 || error.status === 403)
        ) {
            showAuthError(error.message);
            return;
        }

        setStatus(errorMessage(error), "error");
        notify("error");
    }

    async function refreshData() {
        if (!authenticated || refreshing || saving) {
            return;
        }

        refreshing = true;
        updateControls();

        setStatus("Обновляем статистику…", "loading");

        try {
            const [profile, top] = await Promise.all([
                api("/api/me"),
                api("/api/top"),
            ]);

            // При обновлении счётчика не сбрасываем
            // ещё не сохранённый выбор пользователя.
            const selectedAnonymous = (
                elements.anonymousMode.checked
            );

            renderProfile(profile);

            elements.anonymousMode.checked = selectedAnonymous;
            elements.namedMode.checked = !selectedAnonymous;

            renderTop(top);

            setStatus("Статистика обновлена.", "success");
        } catch (error) {
            handleRequestError(error);
        } finally {
            refreshing = false;
            updateControls();
        }
    }

    async function saveProfile(event) {
        event.preventDefault();

        if (!authenticated || saving || refreshing) {
            return;
        }

        saving = true;
        updateControls();

        setStatus("Сохраняем выбор…", "loading");

        let profileSaved = false;

        try {
            const result = await api("/api/profile", {
                method: "POST",
                body: JSON.stringify({
                    is_anonymous: elements.anonymousMode.checked,
                }),
            });

            if (!result || result.ok !== true) {
                throw new ApiError(
                    "Сервер не подтвердил сохранение."
                );
            }

            renderProfile(result.profile);
            profileSaved = true;

            const top = await api("/api/top");
            renderTop(top);

            setStatus("Выбор сохранён.", "success");
            notify("success");
        } catch (error) {
            if (
                profileSaved
                && !(error instanceof ApiError && error.status === 401)
                && !(error instanceof ApiError && error.status === 403)
            ) {
                setStatus(
                    "Выбор сохранён, но рейтинг обновить не удалось. "
                    + "Нажми «Обновить».",
                    "error"
                );
            } else {
                handleRequestError(error);
            }
        } finally {
            saving = false;
            updateControls();
        }
    }

    async function initialize() {
        try {
            telegram?.ready();
            telegram?.expand();
        } catch {
            // Ошибка необязательных методов не блокирует вход.
        }

        initData = (
            typeof telegram?.initData === "string"
                ? telegram.initData
                : ""
        );

        if (!initData) {
            showAuthError(
                "Для входа открой личный чат с ботом "
                + "и нажми «Открыть Mini App». "
                + "При обычном открытии ссылки авторизация недоступна."
            );
            return;
        }

        setStatus("Загружаем профиль и рейтинг…", "loading");

        try {
            const [profile, top] = await Promise.all([
                api("/api/me"),
                api("/api/top"),
            ]);

            renderProfile(profile);
            renderTop(top);

            authenticated = true;

            elements.authError.hidden = true;
            elements.content.hidden = false;

            updateControls();
            setStatus("");
        } catch (error) {
            elements.ratingLoading.hidden = true;

            if (
                error instanceof ApiError
                && (error.status === 401 || error.status === 403)
            ) {
                showAuthError(error.message);
            } else {
                showStartupError(errorMessage(error));
            }
        }
    }

    elements.profileForm.addEventListener(