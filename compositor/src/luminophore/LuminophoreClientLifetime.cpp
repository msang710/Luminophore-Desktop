#include "LuminophoreClientLifetime.hpp"
#include <utility>

using namespace Luminophore;

CClientLifetime::CClientLifetime() {
    wl_list_init(&m_destroy.listener.link);
    m_destroy.listener.notify = destroyed;
    m_destroy.owner           = this;
}
CClientLifetime::~CClientLifetime() {
    reset();
}
bool CClientLifetime::accepts(wl_client* client) const {
    return client && (!m_bound || m_client == client);
}
bool CClientLifetime::bind(wl_client* client, std::function<void()> disconnected) {
    if (!accepts(client))
        return false;
    if (m_bound)
        return true;
    m_bound        = true;
    m_client       = client;
    m_disconnected = std::move(disconnected);
    wl_client_add_destroy_listener(client, &m_destroy.listener);
    return true;
}
bool CClientLifetime::lost() const {
    return m_bound && !m_client;
}
void CClientLifetime::reset() {
    wl_list_remove(&m_destroy.listener.link);
    wl_list_init(&m_destroy.listener.link);
    m_client       = nullptr;
    m_bound        = false;
    m_disconnected = {};
}
void CClientLifetime::destroyed(wl_listener* listener, void*) {
    SListener* wrapper = wl_container_of(listener, wrapper, listener);
    auto&      owner   = *wrapper->owner;
    wl_list_remove(&owner.m_destroy.listener.link);
    wl_list_init(&owner.m_destroy.listener.link);
    owner.m_client = nullptr;
    auto notify    = std::move(owner.m_disconnected);
    if (notify)
        notify();
}
