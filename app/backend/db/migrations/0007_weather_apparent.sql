-- 0007: 기상청 ASOS 95개 지점 + 일 최고 체감온도. PRAGMA 없음: 러너가 한 트랜잭션으로 실행.
-- weather_daily.apparent_max: KST 10~19시 중 기온·습도가 모두 유효한 시각의 여름철 체감온도 최댓값 (NULL = 유효 시각 부족).
-- weather_daily.n_hours: 그 날 유효 시각 수. weather_station.name/source: 지점명과 출처 ('meteostat' | 'kma_asos').
ALTER TABLE weather_daily ADD COLUMN apparent_max REAL CHECK (apparent_max IS NULL OR apparent_max BETWEEN -40 AND 60);
ALTER TABLE weather_daily ADD COLUMN n_hours INTEGER CHECK (n_hours IS NULL OR n_hours BETWEEN 0 AND 24);
ALTER TABLE weather_station ADD COLUMN name TEXT;
ALTER TABLE weather_station ADD COLUMN source TEXT CHECK (source IS NULL OR source IN ('meteostat','kma_asos'));
