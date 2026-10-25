_-- AIEDH matching: Snowflake setup
-- Embeddings are made in Python (trial accounts can't use AI_EMBED).
-- Snowflake stores every student and does the similarity search in SQL.
-- Paste into a Snowsight SQL worksheet and click "Run All". Safe to rerun.

USE ROLE ACCOUNTADMIN;

CREATE WAREHOUSE IF NOT EXISTS AIEDH_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE;

CREATE DATABASE IF NOT EXISTS AIEDH;
CREATE SCHEMA IF NOT EXISTS AIEDH.MATCHING;

USE WAREHOUSE AIEDH_WH;
USE SCHEMA AIEDH.MATCHING;

-- OR REPLACE because the first run made this with 1024 dims. It's empty, so nothing is lost.
CREATE OR REPLACE TABLE STUDENTS (
  STUDENT_ID     STRING PRIMARY KEY,
  NAME           STRING,
  SCHOOL         STRING,
  MAJOR          STRING,
  YEAR           STRING,          -- freshman, sophomore, ...
  STAGE          STRING,          -- where they are in their situation (for "one step ahead" later)
  SITUATION      STRING,          -- short generic phrase, e.g. "left pre-med, haven't told parents"
  INTERESTS      STRING,
  PROFILE_TEXT   STRING,          -- everything above glued into one paragraph; this is what gets embedded
  EMBEDDING      VECTOR(FLOAT, 384),  -- all-MiniLM-L6-v2 outputs 384 numbers
  IS_SYNTHETIC   BOOLEAN DEFAULT TRUE,
  CONSENTED      BOOLEAN DEFAULT TRUE,
  ONLINE         BOOLEAN DEFAULT FALSE,
  PHONE          STRING,
  CREATED_AT     TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
  UPDATED_AT     TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS MATCHES (
  MATCH_ID       STRING PRIMARY KEY,
  STUDENT_A      STRING,
  STUDENT_B      STRING,
  SCORE          FLOAT,
  METHOD         STRING,          -- 'call_merge', 'group_text', or 'in_app'
  INTRO_TEXT     STRING,
  CREATED_AT     TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Sanity check: plain vector math works on the trial (no AI functions involved).
-- Should return 1.0
SELECT VECTOR_COSINE_SIMILARITY([1,2,3]::VECTOR(FLOAT,3), [1,2,3]::VECTOR(FLOAT,3)) AS SHOULD_BE_ONE;