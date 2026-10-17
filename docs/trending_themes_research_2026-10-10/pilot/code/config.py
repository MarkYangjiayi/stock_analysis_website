# Episode / control configuration for the attention-lead pilot.
EPISODES = {
 "cannabis_2018": dict(wiki=["Cannabis in Canada","Cannabis Act","Legality of cannabis"], hn=["cannabis","marijuana"], edgar=["cannabis"]),
 "ev_clean_2020": dict(wiki=["Electric vehicle","Electric car","Renewable energy"], hn=["electric vehicle","electric car","clean energy"], edgar=["electric vehicle"]),
 "spac_2020": dict(wiki=["Special-purpose acquisition company"], hn=["SPAC","SPACs","blank check"], edgar=["special purpose acquisition company"]),
 "metaverse_2021": dict(wiki=["Metaverse"], hn=["metaverse"], edgar=["metaverse"]),
 "genai_2023": dict(wiki=["Generative artificial intelligence","Large language model","ChatGPT"], hn=["ChatGPT","LLM","generative AI"], edgar=["generative AI","large language model"]),
 "ai_power_2024": dict(wiki=["Energy consumption of artificial intelligence","Environmental impact of artificial intelligence","Data center"], hn=["data center power","datacenter power","power grid"], edgar=["AI data center","data center power"]),
 "nuclear_2024": dict(wiki=["Small modular reactor","Nuclear power"], hn=["nuclear power","small modular reactor","SMR"], edgar=["small modular reactor","nuclear energy"]),
 "quantum_2024": dict(wiki=["Quantum computing","Qubit"], hn=["quantum computing","quantum computer"], edgar=["quantum computing"]),
 "glp1_2023": dict(wiki=["Semaglutide","Tirzepatide","GLP-1 receptor agonist"], hn=["Ozempic","semaglutide","GLP-1"], edgar=["GLP-1","obesity"]),
 "humanoid_2025": dict(wiki=["Humanoid robot","Optimus (robot)","Figure AI"], hn=["humanoid","humanoid robot"], edgar=["humanoid"]),
 "stablecoin_2025": dict(wiki=["Stablecoin","GENIUS Act"], hn=["stablecoin","stablecoins"], edgar=["stablecoin"]),
 "space_2025": dict(wiki=["SpaceX Starship","Rocket Lab","Golden Dome (missile defense system)"], hn=["Starship","Rocket Lab"], edgar=["space economy","low earth orbit"]),
 "drones_2025": dict(wiki=["Unmanned aerial vehicle","Drone warfare"], hn=["drone","drones"], edgar=["unmanned aircraft","drone"]),
 "memory_2025": dict(wiki=["High Bandwidth Memory","Dynamic random-access memory"], hn=["HBM","DRAM","high bandwidth memory"], edgar=["high bandwidth memory","HBM"]),
}
CONTROLS = {
 "nft": dict(wiki=["Non-fungible token"], hn=["NFT","NFTs"], edgar=["non-fungible token"]),
 "web3": dict(wiki=["Web3"], hn=["Web3"], edgar=["Web3"]),
 "lk99": dict(wiki=["LK-99"], hn=["LK-99"], edgar=["superconductor"]),
 "rt_superconductor": dict(wiki=["Room-temperature superconductor"], hn=["superconductor"], edgar=["superconductor"]),
 "metaverse_post2022": dict(wiki=["Metaverse"], hn=["metaverse"], edgar=["metaverse"]),
 "cultured_meat": dict(wiki=["Cultured meat"], hn=["lab-grown meat","cultured meat"], edgar=["cultivated meat"]),
 "vertical_farming": dict(wiki=["Vertical farming"], hn=["vertical farming"], edgar=["vertical farming"]),
 "fusion": dict(wiki=["Fusion power"], hn=["fusion power","nuclear fusion"], edgar=["fusion energy"]),
 "vision_pro": dict(wiki=["Apple Vision Pro"], hn=["Vision Pro"], edgar=["spatial computing"]),
 "clubhouse": dict(wiki=["Clubhouse (app)"], hn=["Clubhouse"], edgar=["social audio"]),
}
def all_items(kind):
    s = []
    for d in (EPISODES, CONTROLS):
        for v in d.values():
            for x in v[kind]:
                if x not in s: s.append(x)
    return s
