#include "SettingsGeneration.hpp"
#include "InputCurves.hpp"
#include "DesktopSettings.hpp"
#include <cmath>
#include <algorithm>
#include <sstream>
#include <locale>
#include <toml++/toml.hpp>
#include <openssl/evp.h>
#include <fstream>
#include <regex>
#include <stdexcept>
#include <system_error>
#include <cstdlib>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

using namespace Luminophore::Settings;
static const std::array<std::string, 5> FILES = {"settings.toml", "monitors.toml", "bindings.toml", "placement.toml", "bundles.toml"};
static bool                             generationID(const std::string& id) {
    return id.size() == 64 && id.find_first_not_of("0123456789abcdef") == std::string::npos;
}
static std::string readText(const std::filesystem::path& path, size_t limit) {
    if (std::filesystem::is_symlink(path) || !std::filesystem::is_regular_file(path) || std::filesystem::file_size(path) > limit)
        throw std::runtime_error("invalid settings file");
    std::ifstream in(path, std::ios::binary);
    std::string   text((std::istreambuf_iterator<char>(in)), {});
    if (in.bad() || text.size() > limit)
        throw std::runtime_error("settings read failed");
    return text;
}
static std::string hashDocuments(const std::array<std::string, 5>& docs) {
    std::string data = "luminophore-settings-v1";
    data += '\0';
    for (size_t i = 0; i < FILES.size(); ++i) {
        data += FILES[i];
        data += '\0';
        data += std::to_string(docs[i].size());
        data += '\0';
        data += docs[i];
    }
    std::array<unsigned char, EVP_MAX_MD_SIZE> bytes{};
    unsigned                                   length = 0;
    if (EVP_Digest(data.data(), data.size(), bytes.data(), &length, EVP_sha256(), nullptr) != 1 || length != 32)
        throw std::runtime_error("settings digest failed");
    std::string result;
    for (unsigned i = 0; i < length; ++i) {
        result += "0123456789abcdef"[bytes[i] >> 4];
        result += "0123456789abcdef"[bytes[i] & 15];
    }
    return result;
}
CSettingsGenerations::CSettingsGenerations(std::filesystem::path root) : m_root(std::move(root)) {}
static Value typedInput(const std::string& key, const toml::node& node) {
    const auto all  = defaults();
    auto       name = "input." + key;
    if (!all.contains(name))
        name = "touchpad." + key;
    if (!all.contains(name))
        name = "touchdevice." + key;
    if (!all.contains(name))
        name = "tablet." + key;
    if (!all.contains(name))
        name = "tablettool." + key;
    if (key == "drag_threshold" || key == "scroll_event_delay" || key == "cursor_inactive_timeout" || key == "cursor_no_warps" || key == "cursor_persistent_warps" ||
        key == "cursor_hide_on_key_press" || key == "cursor_hide_on_touch" || key == "cursor_hide_on_tablet" || key == "cursor_warp_back_after_non_mouse_input" ||
        key == "resize_on_border" || key == "extend_border_grab_area" || key == "resize_on_border_inner_area" || key == "hover_icon_on_border" || key == "resize_corner" ||
        key == "close_gesture_timeout")
        throw std::runtime_error("interaction settings are global-only");
    if (key == "focus_on_close" || key == "float_switch_override_focus" || key == "follow_mouse" || key == "follow_mouse_threshold" || key == "mouse_refocus" ||
        key == "follow_mouse_shrink" || key == "off_window_axis_events" || key == "emulate_discrete_scroll" || key == "enabled" || key == "force_no_accel" || !all.contains(name))
        throw std::runtime_error("unsupported device field");
    Value value = all.at(name);
    if (std::holds_alternative<int64_t>(value) && node.is_integer())
        value = *node.value<int64_t>();
    else if (std::holds_alternative<double>(value) && node.is_floating_point())
        value = *node.value<double>();
    else if (std::holds_alternative<bool>(value) && node.is_boolean())
        value = *node.value<bool>();
    else if (std::holds_alternative<std::string>(value) && node.is_string())
        value = *node.value<std::string>();
    else
        throw std::runtime_error("device settings type mismatch");
    if (!validate({{name, value}}).empty())
        throw std::runtime_error("invalid device setting");
    return value;
}
static void validateKeymapText(const Snapshot& values, const std::string& prefix) {
    static const std::regex allowed("[A-Za-z0-9_:+.,() -]*");
    for (const auto& [key, value] : values) {
        if (!key.starts_with(prefix + "kb_") || key == prefix + "kb_file" || key == prefix + "kb_snapshot")
            continue;
        const auto& text = std::get<std::string>(value);
        if (text.size() > 512 || !std::regex_match(text, allowed))
            throw std::runtime_error("invalid keymap text");
    }
}
SInputCurve Luminophore::Settings::parseInputCurve(const std::string& text, bool profile) {
    if (text.size() > 4096)
        throw std::runtime_error("acceleration curve too long");
    if (text.empty() || (profile && (text == "adaptive" || text == "flat")))
        return {};
    if (text.find_first_of("\t\n\r\v\f") != std::string::npos)
        throw std::runtime_error("acceleration curve requires ASCII spaces");
    std::istringstream input(text);
    input.imbue(std::locale::classic());
    std::string token;
    if (profile && (!text.starts_with("custom ") || !(input >> token) || token != "custom"))
        throw std::runtime_error("unknown acceleration profile");
    static const std::regex number(R"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)");
    std::vector<double>     values;
    while (input >> token) {
        if (!std::regex_match(token, number) || values.size() >= 65)
            throw std::runtime_error("invalid acceleration number/count");
        std::istringstream numeric(token);
        numeric.imbue(std::locale::classic());
        double value = 0;
        if (!(numeric >> value) || !std::isfinite(value) || value < 0)
            throw std::runtime_error("invalid acceleration value");
        values.push_back(value);
    }
    if (values.size() < 3 || values.front() <= 0)
        throw std::runtime_error("acceleration needs positive step and 2..64 points");
    return {values.front(), {values.begin() + 1, values.end()}};
}
static void validateAcceleration(const Snapshot& values, const std::string& prefix) {
    const auto motion = parseInputCurve(std::get<std::string>(values.at(prefix + "accel_profile")), true);
    const auto scroll = parseInputCurve(std::get<std::string>(values.at(prefix + "scroll_points")), false);
    if (!scroll.points.empty() && motion.points.empty())
        throw std::runtime_error("scroll_points requires custom acceleration");
}
SGeneration CSettingsGenerations::load(const std::string& id) const {
    if (!generationID(id))
        throw std::runtime_error("invalid generation id");
    const auto dir = m_root / "generations" / id;
    if (std::filesystem::is_symlink(dir))
        throw std::runtime_error("invalid generation directory");
    std::array<std::string, 5> docs;
    size_t                     size = 0;
    for (size_t i = 0; i < FILES.size(); ++i) {
        docs[i] = readText(dir / FILES[i], 4 * 1024 * 1024);
        size += docs[i].size();
        if (size > 4 * 1024 * 1024)
            throw std::runtime_error("settings size limit");
    }
    if (hashDocuments(docs) != id)
        throw std::runtime_error("settings digest mismatch");
    SGeneration result{id, defaults(), 44, {}};
    result.documents = docs;
    decodeDesktopSettings(docs);
    for (size_t i = 0; i < FILES.size(); ++i) {
        if (i == 1) {
            result.monitors = decodeMonitorSettings(docs[i]);
            continue;
        }
        if (i > 1)
            continue; // validated as owned desktop domains above
        const auto table = toml::parse(docs[i]);
        if (!table["schema_version"].is_integer() || table["schema_version"].value<int64_t>() != 1)
            throw std::runtime_error("settings schema version");
        for (const auto& [section, node] : table) {
            if (section == "schema_version")
                continue;
            if (section == "native")
                continue;
            if (i != 0 || !node.is_table())
                throw std::runtime_error("field outside settings fixture");
            if (section == "devices") {
                if (node.as_table()->size() > 32)
                    throw std::runtime_error("too many device rules");
                static const std::regex selector("[a-z0-9_-]{1,128}");
                for (const auto& [name, rule] : *node.as_table()) {
                    if (!std::regex_match(std::string(name.str()), selector) || !rule.is_table())
                        throw std::runtime_error("invalid device rule");
                    auto& dest = result.devices[std::string(name.str())];
                    for (const auto& [key, value] : *rule.as_table())
                        dest[std::string(key.str())] = typedInput(std::string(key.str()), value);
                    validateKeymapText(dest, "");
                }
                continue;
            }
            for (const auto& [field, value] : *node.as_table()) {
                const auto key = std::string(section.str()) + "." + std::string(field.str());
                if (key == "layout.panel_height") {
                    const auto height = value.value<int64_t>();
                    if (!value.is_integer() || !height || *height < 24 || *height > 240)
                        throw std::runtime_error("invalid panel height");
                    result.panelHeight = *height;
                    continue;
                }
                if (!result.values.contains(key)) {
                    validateShellField(key, value);
                    continue;
                }
                auto& dest = result.values.at(key);
                if (std::holds_alternative<int64_t>(dest) && value.is_integer())
                    dest = *value.value<int64_t>();
                else if (std::holds_alternative<double>(dest) && value.is_floating_point())
                    dest = *value.value<double>();
                else if (std::holds_alternative<bool>(dest) && value.is_boolean())
                    dest = *value.value<bool>();
                else if (std::holds_alternative<std::string>(dest) && value.is_string())
                    dest = *value.value<std::string>();
                else
                    throw std::runtime_error("settings type mismatch");
            }
        }
    }
    validateGenerationInputs(result);
    return result;
}
void Luminophore::Settings::validateGenerationInputs(const SGeneration& result) {
    if (result.values.size() != defaults().size() || !validate(result.values).empty() || result.devices.size() > 32)
        throw std::runtime_error("invalid generation input schema");
    static const std::regex selector("[a-z0-9_-]{1,128}");
    for (const auto& [name, fields] : result.devices) {
        if (!std::regex_match(name, selector))
            throw std::runtime_error("invalid device selector");
        for (const auto& [key, value] : fields)
            std::visit([&](const auto& item) { typedInput(key, toml::value<std::decay_t<decltype(item)>>{item}); }, value);
        validateKeymapText(fields, "");
    }
    auto output = [](const Value& value) {
        const auto&             text = std::get<std::string>(value);
        static const std::regex selector("[A-Za-z0-9_-]{1,128}");
        if (!text.empty() && text != "[[Auto]]" && !std::regex_match(text, selector))
            throw std::runtime_error("invalid input output selector");
    };
    output(result.values.at("touchdevice.output"));
    output(result.values.at("tablet.output"));
    auto pressure = [](double lo, double hi) {
        if (lo >= 0 && hi >= 0 && lo > hi)
            throw std::runtime_error("invalid tablet pressure range");
    };
    const auto lo = std::get<double>(result.values.at("tablettool.pressure_range_min")), hi = std::get<double>(result.values.at("tablettool.pressure_range_max"));
    pressure(lo, hi);
    for (const auto& [name, fields] : result.devices) {
        if (fields.contains("output"))
            output(fields.at("output"));
        pressure(fields.contains("pressure_range_min") ? std::get<double>(fields.at("pressure_range_min")) : lo,
                 fields.contains("pressure_range_max") ? std::get<double>(fields.at("pressure_range_max")) : hi);
    }
    validateAcceleration(result.values, "input.");
    for (const auto& [name, fields] : result.devices) {
        Snapshot effective;
        for (const auto* key : {"accel_profile", "scroll_points"})
            effective[key] = fields.contains(key) ? fields.at(key) : result.values.at(std::string("input.") + key);
        validateAcceleration(effective, "");
    }
    auto keymap = [](const std::string& path, const std::string& snapshot) {
        if (path.empty()) {
            if (!snapshot.empty())
                throw std::runtime_error("keymap snapshot without file");
            return;
        }
        if (!std::filesystem::path(path).is_absolute() || path.size() > 4096 || std::any_of(path.begin(), path.end(), [](unsigned char c) { return c < 32; }) ||
            !snapshot.starts_with(path + "\n") || snapshot.size() <= path.size() + 1 || snapshot.size() > path.size() + 1 + 1024 * 1024 || snapshot.find('\0') != std::string::npos)
            throw std::runtime_error("invalid imported keymap snapshot");
    };
    const auto file = std::get<std::string>(result.values.at("input.kb_file")), snapshot = std::get<std::string>(result.values.at("input.kb_snapshot"));
    keymap(file, snapshot);
    for (const auto& [name, fields] : result.devices)
        keymap(fields.contains("kb_file") ? std::get<std::string>(fields.at("kb_file")) : file,
               fields.contains("kb_snapshot") ? std::get<std::string>(fields.at("kb_snapshot")) : snapshot);
    validateKeymapText(result.values, "input.");
    if (!validate(result.values).empty())
        throw std::runtime_error("invalid generation values");
}
SGeneration CSettingsGenerations::completed() const {
    const auto       text = readText(m_root / "completed.json", 4096);
    const std::regex pattern(R"json(\s*\{\s*"generation"\s*:\s*"([0-9a-f]{64})"\s*\}\s*)json");
    std::smatch      match;
    if (!std::regex_match(text, match, pattern))
        throw std::runtime_error("invalid completed pointer");
    return load(match[1]);
}
static void syncPath(const std::filesystem::path& path) {
    const int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        throw std::runtime_error("settings sync open failed");
    const int result = fsync(fd);
    close(fd);
    if (result != 0)
        throw std::runtime_error("settings sync failed");
}
SGeneration CSettingsGenerations::bootstrap() const {
    // Only native startup may initialize an empty fixture; never publish a
    // pending candidate. The ordinary Python store uses the same lock.
    std::filesystem::create_directories(m_root / "generations");
    const int fd = open((m_root / "write.lock").c_str(), O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0)
        throw std::runtime_error("settings lock unavailable");
    struct SLock {
        int fd;
        ~SLock() {
            flock(fd, LOCK_UN);
            close(fd);
        }
    } lock{fd};
    if (flock(fd, LOCK_EX) != 0)
        throw std::runtime_error("settings lock failed");
    if (std::filesystem::exists(m_root / "completed.json"))
        return completed();
    std::array<std::string, 5> docs;
    docs.fill("schema_version = 1\n");
    // The session runner stages all five files before starting a first native
    // session. Never silently replace an incomplete migration with defaults.
    const auto config = std::getenv("LUMINOPHORE_CONFIG_ROOT");
    if (config && *config) {
        if (!std::filesystem::path(config).is_absolute())
            throw std::runtime_error("absolute Luminophore config root required");
        size_t total = 0;
        for (size_t i = 0; i < FILES.size(); ++i) {
            const auto path = std::filesystem::path(config) / FILES[i];
            if (std::filesystem::is_symlink(path) || !std::filesystem::is_regular_file(path) || (total += std::filesystem::file_size(path)) > 4 * 1024 * 1024)
                throw std::runtime_error("complete native TOML bundle required: " + path.string());
            std::ifstream input(path, std::ios::binary);
            docs[i] = std::string(std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>());
        }
    } else if (!settingsFixtureEnabled())
        throw std::runtime_error("start through the Luminophore session launcher to initialize settings");
    const auto id  = hashDocuments(docs);
    const auto dir = m_root / "generations" / id;
    std::filesystem::create_directories(dir);
    for (size_t i = 0; i < FILES.size(); ++i) {
        auto path = dir / FILES[i];
        if (!std::filesystem::exists(path)) {
            std::ofstream out(path, std::ios::binary);
            out << docs[i];
            out.close();
            if (!out)
                throw std::runtime_error("settings bootstrap write failed");
            syncPath(path);
        }
    }
    const auto verified = load(id);
    syncPath(dir);
    syncPath(m_root / "generations");
    const auto temp = m_root / (".bootstrap-" + std::to_string(getpid()));
    {
        std::ofstream out(temp);
        out << "{\"generation\":\"" << id << "\"}";
        out.close();
        if (!out)
            throw std::runtime_error("settings pointer write failed");
    }
    syncPath(temp);
    std::filesystem::rename(temp, m_root / "completed.json");
    syncPath(m_root);
    syncPath(m_root.parent_path());
    return verified;
}
void Luminophore::Settings::selectSafeSettings(const std::string& reason) {
    const auto  runtime = std::getenv("XDG_RUNTIME_DIR");
    struct stat info{};
    if (!runtime || !std::filesystem::path(runtime).is_absolute() || lstat(runtime, &info) != 0 || !S_ISDIR(info.st_mode) || info.st_uid != getuid() || (info.st_mode & 0077))
        throw std::runtime_error("safe settings require an owned private runtime directory");
    auto       pattern = (std::filesystem::path(runtime) / "luminophore-settings-recovery-XXXXXX").string();
    const auto created = mkdtemp(pattern.data());
    if (!created)
        throw std::runtime_error("cannot create isolated recovery settings");
    const auto root   = std::filesystem::path(created);
    const auto config = root / "config";
    std::filesystem::create_directory(config);
    const auto role    = std::getenv("LUMINOPHORE_SESSION_ROLE");
    const bool greeter = role && std::string_view(role) == "greeter";
    for (const auto& name : FILES) {
        std::ofstream out(config / name);
        out << "schema_version = 1\n";
        if (name == "settings.toml")
            out << "[motion]\nenabled = false\n[native]\nprofile = \"" << (greeter ? "greeter" : "desktop") << "\"\n";
        out.close();
        if (!out)
            throw std::runtime_error("cannot write safe settings");
        syncPath(config / name);
    }
    std::ofstream report(root / "reason.txt");
    report << reason << '\n';
    report.close();
    if (!report)
        throw std::runtime_error("cannot preserve recovery reason");
    syncPath(root / "reason.txt");
    syncPath(config);
    syncPath(root);
    // The original state/config remains untouched. This private override is
    // stripped when launching ordinary applications, unlike XDG_STATE_HOME.
    if (setenv("LUMINOPHORE_CONFIG_ROOT", config.c_str(), 1) || setenv("LUMINOPHORE_SETTINGS_STATE_ROOT", (root / "state").c_str(), 1) ||
        setenv("LUMINOPHORE_SETTINGS_RECOVERY", "1", 1))
        throw std::runtime_error("cannot select safe settings");
}

bool Luminophore::Settings::settingsFixtureEnabled() {
    const auto flag = std::getenv("LUMINOPHORE_SETTINGS_FIXTURE");
    return flag && std::string(flag) == "1";
}
std::filesystem::path Luminophore::Settings::settingsFixtureRoot() {
    const auto recovery = std::getenv("LUMINOPHORE_SETTINGS_STATE_ROOT");
    if (recovery && *recovery) {
        if (!std::filesystem::path(recovery).is_absolute())
            throw std::runtime_error("absolute private settings root required");
        return std::filesystem::path(recovery) / "settings";
    }
    if (settingsFixtureEnabled())
        for (const auto* name : {"XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"}) {
            const auto value = std::getenv(name);
            if (!value || !std::filesystem::path(value).is_absolute())
                throw std::runtime_error("fixture requires explicit absolute XDG paths");
        }
    const auto state = std::getenv("XDG_STATE_HOME");
    if (state && *state) {
        if (!std::filesystem::path(state).is_absolute())
            throw std::runtime_error("absolute XDG state path required");
        return std::filesystem::path(state) / "luminophore/settings";
    }
    const auto home = std::getenv("HOME");
    if (!home || !std::filesystem::path(home).is_absolute())
        throw std::runtime_error("absolute HOME required");
    return std::filesystem::path(home) / ".local/state/luminophore/settings";
}

CSettingsLease::CSettingsLease(const std::filesystem::path& path) {
    std::filesystem::create_directories(path.parent_path());
    m_fd = open(path.c_str(), O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (m_fd < 0)
        throw std::runtime_error("settings lease unavailable");
    if (flock(m_fd, LOCK_EX | LOCK_NB) != 0) {
        close(m_fd);
        m_fd = -1;
        throw std::runtime_error("settings owner still active");
    }
}
CSettingsLease::~CSettingsLease() {
    if (m_fd >= 0) {
        flock(m_fd, LOCK_UN);
        close(m_fd);
    }
}
SGeneration CSettingsGenerations::recover(const std::string& epoch) const {
    if (epoch.size() != 32 || epoch.find_first_not_of("0123456789abcdef") != std::string::npos)
        throw std::runtime_error("invalid settings epoch");
    const int fd = open((m_root / "write.lock").c_str(), O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0)
        throw std::runtime_error("settings lock unavailable");
    struct SLock {
        int fd;
        ~SLock() {
            flock(fd, LOCK_UN);
            close(fd);
        }
    } lock{fd};
    if (flock(fd, LOCK_EX) != 0)
        throw std::runtime_error("settings lock failed");
    const auto verified = completed();
    // Same lock as every Python mutation: all earlier writes have finished,
    // and queued old-epoch writes must fail before touching the filesystem.
    const auto temp = m_root / (".epoch-" + epoch);
    {
        std::ofstream out(temp);
        out << "{\"epoch\":\"" << epoch << "\"}";
        out.close();
        if (!out)
            throw std::runtime_error("epoch write failed");
    }
    syncPath(temp);
    std::filesystem::rename(temp, m_root / "coordinator.json");
    syncPath(m_root);
    std::filesystem::remove(m_root / "pending.json");
    syncPath(m_root);
    return verified;
}
