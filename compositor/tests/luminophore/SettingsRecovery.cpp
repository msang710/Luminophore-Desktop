#include "../../src/config/luminophore/SettingsGeneration.hpp"
#include "../../src/config/luminophore/DesktopSettings.hpp"
#include <gtest/gtest.h>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <optional>

using namespace Luminophore::Settings;

class CSettingsRecoveryTest : public ::testing::Test {
  protected:
    std::filesystem::path                             root;
    std::map<std::string, std::optional<std::string>> previous;
    void                                              SetUp() override {
        auto       pattern = (std::filesystem::temp_directory_path() / "luminophore-recovery-test-XXXXXX").string();
        const auto path    = mkdtemp(pattern.data());
        ASSERT_NE(path, nullptr);
        root = path;
        for (const auto key : {"XDG_RUNTIME_DIR", "LUMINOPHORE_CONFIG_ROOT", "LUMINOPHORE_SETTINGS_STATE_ROOT", "LUMINOPHORE_SETTINGS_RECOVERY", "LUMINOPHORE_SESSION_ROLE"}) {
            const auto value = std::getenv(key);
            previous[key]    = value ? std::optional<std::string>{value} : std::nullopt;
            unsetenv(key);
        }
        setenv("XDG_RUNTIME_DIR", root.c_str(), 1);
    }
    void TearDown() override {
        for (const auto& [key, value] : previous) {
            if (value)
                setenv(key.c_str(), value->c_str(), 1);
            else
                unsetenv(key.c_str());
        }
        std::filesystem::remove_all(root);
    }
};

TEST_F(CSettingsRecoveryTest, SafeGenerationPreservesRejectedSource) {
    const auto original = root / "original";
    std::filesystem::create_directory(original);
    std::ofstream(original / "completed.json") << "damaged original";
    setenv("LUMINOPHORE_SETTINGS_STATE_ROOT", original.c_str(), 1);
    selectSafeSettings("invalid checkpoint");
    const auto selected = settingsFixtureRoot();
    EXPECT_NE(selected, original / "settings");
    EXPECT_TRUE(selected.string().starts_with(root.string()));
    const auto boot = CSettingsGenerations(selected).bootstrap();
    EXPECT_EQ(CSettingsGenerations(selected).completed().id, boot.id);
    EXPECT_FALSE(std::get<bool>(boot.values.at("motion.enabled")));
    EXPECT_FALSE(decodeDesktopSettings(boot.documents).bindings.empty());
    std::ifstream preserved(original / "completed.json");
    std::string   text;
    std::getline(preserved, text);
    EXPECT_EQ(text, "damaged original");
    EXPECT_STREQ(std::getenv("LUMINOPHORE_SETTINGS_RECOVERY"), "1");
}

TEST_F(CSettingsRecoveryTest, SafeGreeterDoesNotAcquireDesktopBindings) {
    setenv("LUMINOPHORE_SESSION_ROLE", "greeter", 1);
    selectSafeSettings("invalid greeter generation");
    const auto boot    = CSettingsGenerations(settingsFixtureRoot()).bootstrap();
    const auto desktop = decodeDesktopSettings(boot.documents);
    EXPECT_TRUE(desktop.bindings.empty());
    EXPECT_EQ(desktop.native["profile"].value_or(std::string{}), "greeter");
}

TEST_F(CSettingsRecoveryTest, NonPrivateRuntimeIsRejected) {
    std::filesystem::permissions(root, std::filesystem::perms::all);
    EXPECT_THROW(selectSafeSettings("invalid"), std::runtime_error);
    EXPECT_EQ(std::getenv("LUMINOPHORE_SETTINGS_RECOVERY"), nullptr);
}
