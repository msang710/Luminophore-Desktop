#pragma once

#include "WorkspaceStateTracker.hpp"

#include "../SharedDefs.hpp"

#include <optional>
#include <ranges>
#include <string>
#include <unordered_map>

namespace State {
    class CWorkspaceStateTracker : public IWorkspaceStateTracker {
      public:
        CWorkspaceStateTracker()                   = default;
        virtual ~CWorkspaceStateTracker() override = default;

        virtual const std::vector<PHLWORKSPACEREF>& workspaceRefs() const override;
        virtual std::vector<SWorkspaceQueryable>    queryableWorkspaces() const override;
        auto                                        workspaces() const {
            return std::views::filter(m_workspaces, [](const auto& e) { return !!e; });
        }
        std::vector<PHLWORKSPACE>  workspacesCopy() const;
        std::vector<PHLWORKSPACE>  userWorkspaces() const;

        void                       add(PHLWORKSPACE w);
        void                       clear();

        [[nodiscard]] PHLWORKSPACE create(const WORKSPACEID& id, const MONITORID& monid, const std::string& name = "", bool isEmpty = true);
        [[nodiscard]] PHLWORKSPACE baseForMonitor(const PHLMONITORREF& monitor) const;
        [[nodiscard]] PHLWORKSPACE ensureBaseForMonitor(const PHLMONITOR& monitor);
        WORKSPACEID                nextAvailableNamedWorkspace() const;
        WORKSPACEID                newSpecialID() const;
        bool                       isSpecial(const WORKSPACEID& id) const;

      private:
        std::vector<PHLWORKSPACEREF> m_workspaces;
    };

    UP<CWorkspaceStateTracker>& workspaceState();
}
